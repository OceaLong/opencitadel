"""Legal current commands and real projections for scoped parallel/tied events."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from strict_driver.budget import command
from strict_driver.ownership import KERNEL
from strict_driver.recovery import assertion, scenario

from app.composition.execution_content import build_execution_view_service
from app.domain.execution.run import RunAggregate, RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator


async def parallel_views(context):
    now = datetime.now(UTC)
    clock = [now]
    run_id = uuid4()
    active = await context.shared.runtime_policy_reader.active_execution(
        require_fresh=True, now=now
    )
    snapshot = derive_run_policy_snapshot(active, RunFamily.AGENT)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=context.resources.postgres.session_factory,
        aggregates={"run": RunAggregate()},
        authorization=KERNEL,
        formal_now=lambda: clock[0],
    )

    async def send(kind, payload=None, version=1):
        result = await handler.handle(command(context, run_id, kind, payload, version))
        assertion(kind + "_accepted", result.status == "accepted")

    # New Run identity is the command's actual resource; source session was made
    # through public API and journaled by bootstrap. No persisted time rewrite.
    context.own("run", run_id)
    await send(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": context.input.bootstrap.session_id,
            "semantic_payload": {},
            "public_input": {"message": "acceptance parallel event semantics"},
            "policy_snapshot": snapshot.model_dump(mode="json"),
        },
    )
    await send("StartRun")
    root, direct, missing = uuid4(), uuid4(), uuid4()

    async def request(identity, kind, parent):
        await send(
            "RequestActivity",
            {
                "activity_id": str(identity),
                "activity_type": kind,
                "timeout_at": (now + timedelta(minutes=5)).isoformat(),
                "input_digest": "acceptance-public-semantic-probe",
                "parent_activity_id": str(parent) if parent else None,
                "invocation_id": None,
                "public_data": {"tool_name": "parallel_probe"} if kind == "tool.call" else {},
            },
            version=2,
        )
        context.own("activity", identity)

    await request(root, "model.call", None)
    # The formal projection links only a unique completed parent at the
    # child's request. Settle the root first while keeping the two children
    # concurrent and their controlled start/end times tied.
    await send(
        "MarkActivityCallStarted",
        {"activity_id": str(root), "generation": 0, "claim_generation": 1},
        version=2,
    )
    await send(
        "CompleteActivity",
        {
            "activity_id": str(root),
            "generation": 0,
            "claim_generation": 1,
            "public_data": {"success": True},
        },
        version=2,
    )
    await request(direct, "tool.call", root)
    await request(missing, "tool.call", None)
    clock[0] = now + timedelta(seconds=1)
    for identity in (direct, missing):
        await send(
            "MarkActivityCallStarted",
            {"activity_id": str(identity), "generation": 0, "claim_generation": 1},
            version=2,
        )
    clock[0] = now + timedelta(seconds=3)
    for identity in (direct, missing):
        await send(
            "CompleteActivity",
            {
                "activity_id": str(identity),
                "generation": 0,
                "claim_generation": 1,
                "public_data": {"success": identity != missing},
            },
            version=2,
        )
    await send("CompleteRun")
    # The view's observation boundary must be no earlier than the controlled
    # formal terminal event, otherwise valid future-dated facts are clipped.
    until_terminal = (clock[0] - datetime.now(UTC)).total_seconds()
    if until_terminal > 0:
        await asyncio.sleep(until_terminal + 0.05)
    projector = PostgresFormalProjector(
        session_factory=context.resources.postgres.session_factory, authorization=KERNEL
    )
    for _ in range(20):
        result = await projector.run_once(context.scope, limit=100, notify=False)
        if not result.processed:
            break
    else:
        raise RuntimeError("owned source projection did not converge")
    authorized = AuthorizationContext.for_principal(context.principal, scope=context.scope)
    views = build_execution_view_service(
        settings=context.settings, resources=context.resources, authorization=authorized
    )
    view = await views.get_view(context.scope, run_id)
    steps = {step.activity_id: step for step in view.steps}
    if not (
        view.run.duration_ms == 3000
        and steps[direct].duration_ms == steps[missing].duration_ms == 2000
    ):
        context.report["failure_state"] = {
            "run_duration_ms": view.run.duration_ms,
            "direct_duration_ms": steps[direct].duration_ms,
            "missing_duration_ms": steps[missing].duration_ms,
            "root_duration_ms": steps[root].duration_ms,
        }
    assertions = [
        assertion("one_step_per_actual_activity", len(view.steps) == len(steps) == 3),
        assertion(
            "tied_start_time_preserved", steps[direct].started_at == steps[missing].started_at
        ),
        assertion(
            "parallel_elapsed_not_sum",
            view.run.duration_ms == 3000
            and steps[direct].duration_ms == steps[missing].duration_ms == 2000,
        ),
    ]
    resources = {"run_id": str(run_id), "session_id": context.input.bootstrap.session_id}
    observation = view.model_dump(mode="json")
    for identity, checks in (
        ("parallel_order", assertions),
        (
            "missing_parent",
            [
                assertion("known_parent_link", steps[direct].parent_step_id == steps[root].step_id),
                assertion(
                    "absent_parent_unknown",
                    steps[missing].parent_step_id is None
                    and steps[missing].relationship == "unknown",
                ),
                assertion(
                    "missing_parent_explicit",
                    "parent_step_id" in steps[missing].completeness.missing_fields,
                ),
            ],
        ),
        (
            "business_failure_successful_run",
            [
                assertion(
                    "business_failure_preserved", steps[missing].business_outcome == "failure"
                ),
                assertion("run_not_misclassified", view.run.status.value == "completed"),
            ],
        ),
    ):
        item = scenario(
            identity,
            resources,
            {
                "source_session": context.input.bootstrap.session_id,
                "run_created_at": now.isoformat(),
            },
            observation,
            checks,
            mechanism="legal_commands_with_injected_orchestrator_clock",
        )
        item["requirement"] = "AC02"
        item["cleanup"] = {
            "state": "retained_immutable",
            "parent_session_id": context.input.bootstrap.session_id,
        }
        context.scenarios.append(item)
    port = PostgresExecutionView(
        session_factory=context.resources.postgres.session_factory, authorization=authorized
    )
    rebuilt = await port.rebuild_scope_shadow(context.scope)
    after = await views.get_view(context.scope, run_id)
    assertions = [
        assertion("shadow_activated", rebuilt.activated),
        assertion(
            "public_semantics_preserved",
            after.run.status == view.run.status and after.steps == view.steps,
        ),
        assertion("watermark_preserved", after.revision == view.revision),
    ]
    item = scenario(
        "shadow_activation", resources, observation, after.model_dump(mode="json"), assertions
    )
    item["requirement"] = "AC05"
    item["cleanup"] = {"state": "retained_immutable", "generation": rebuilt.generation}
    context.scenarios.append(item)


async def dated_analysis_runs(context):
    """Legal commands with controlled dates, never a database timestamp rewrite."""
    from collections import Counter

    from strict_driver.dated_plan import dated_plan

    from app.domain.analysis.metrics import calendar_bucket

    now = datetime.now(UTC)
    start, end, instants = dated_plan(now)
    session_id = context.input.bootstrap.analysis_session_id
    active = await context.shared.runtime_policy_reader.active_execution(
        require_fresh=True, now=now
    )
    snapshot = derive_run_policy_snapshot(active, RunFamily.AGENT)
    clock = [now]
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=context.resources.postgres.session_factory,
        aggregates={"run": RunAggregate()},
        authorization=KERNEL,
        formal_now=lambda: clock[0],
    )
    identities = []
    for instant in instants:
        run_id = uuid4()
        context.own("run", run_id)
        identities.append(str(run_id))
        clock[0] = instant
        for kind, payload in (
            (
                "CreateRun",
                {
                    "family": "agent",
                    "source_entity_type": "session",
                    "source_entity_id": session_id,
                    "semantic_payload": {},
                    "public_input": {"message": "acceptance controlled historical bucket"},
                    "policy_snapshot": snapshot.model_dump(mode="json"),
                },
            ),
            ("StartRun", None),
            ("CompleteRun", None),
        ):
            if kind == "CompleteRun":
                clock[0] = instant + timedelta(seconds=3)
            envelope = command(context, run_id, kind, payload).model_copy(
                update={"issued_at": clock[0]}
            )
            result = await handler.handle(envelope)
            assertion(kind + "_accepted", result.status == "accepted")
    projector = PostgresFormalProjector(
        session_factory=context.resources.postgres.session_factory, authorization=KERNEL
    )
    for _ in range(30):
        projected = await projector.run_once(context.scope, limit=100, notify=False)
        if not projected.processed:
            break
    else:
        raise RuntimeError("dated source projection did not converge")
    authorized = AuthorizationContext.for_principal(context.principal, scope=context.scope)
    views = build_execution_view_service(
        settings=context.settings, resources=context.resources, authorization=authorized
    )
    observations = []
    admitted = []
    for identity in identities:
        from uuid import UUID

        view = await views.get_view(context.scope, UUID(identity))
        assertion(
            "dated_run_completed",
            view.run.status.value == "completed" and view.run.duration_ms == 3000,
        )
        admitted.append(view.run.admitted_at)
        observations.append(view.model_dump(mode="json"))
    filters = {"session": session_id, "start": start.isoformat(), "end": end.isoformat()}
    # This controlled producer owns the kernel role. The public summary
    # function is API-role-only and is exercised by the Playwright AC19 test
    # against these exact source IDs after this producer exits.
    buckets = Counter(calendar_bucket(instant, "day", "UTC")[0].isoformat() for instant in admitted)
    checks = [
        assertion("utc_preference", all(bucket.endswith("+00:00") for bucket in buckets)),
        assertion(
            "seven_distinct_buckets",
            len(buckets) == 7,
        ),
        assertion("one_run_each", all(count == 1 for count in buckets.values())),
    ]
    item = scenario(
        "seven_daily_buckets",
        {"session_id": session_id, "run_ids": identities},
        {
            "filters": filters,
            "policy_observed_at": now.isoformat(),
            "provenance": "controlled historical event clock with current policy; no elapsed-days claim",
        },
        {
            "views": observations,
            "source_bucket_counts": dict(buckets),
            "timezone": "UTC",
        },
        checks,
        mechanism="legal_commands_with_injected_orchestrator_clock",
    )
    item["requirement"] = "AC19"
    item["test_id"] = "strict_driver.views.dated_analysis_runs"
    item["cleanup"] = {"state": "retained_immutable", "parent_session_id": session_id}
    context.scenarios.append(item)
