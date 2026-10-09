"""Bounded real-provider dispatch, conservative contention and delayed settlement."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from strict_driver.ownership import KERNEL, assert_exclusive
from strict_driver.recovery import assertion, scenario

from app.application.evaluation.batch_service import BatchService
from app.application.execution.decisions.base import activity_identity
from app.application.execution.run_context import run_execution_context
from app.composition.evaluation import build_budget_authority
from app.composition.evaluation_execution import configured_execution_policy
from app.composition.physical_budget import configured_physical_policy
from app.domain.execution.activity import ActivityContext
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate, RunState
from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.external.llm.base_llm import normalize_usage


def command(context, run_id, kind, payload=None, version=1):
    return CommandEnvelope(
        command_id=uuid4(),
        command_type=kind,
        command_schema_version=version,
        stream_type="run",
        stream_id=str(run_id),
        owner_user_id=context.scope.user_id,
        team_id=context.scope.team_id,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload=payload or {},
    )


async def state_of(context, run_id):
    async with context.shared.uow_factory(KERNEL) as work:
        value = await work.db_session.scalar(
            text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"), {"run": run_id}
        )
        return RunState.model_validate(value)


async def budget_and_late_cancel(context):
    await assert_exclusive(context.shared.uow_factory, context.allowed)
    session_factory = context.resources.postgres.session_factory
    policy = configured_execution_policy(context.settings)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=session_factory,
        aggregates={"run": RunAggregate()},
        authorization=KERNEL,
        evaluation_execution=EvaluationExecutionGuard(
            policy, session_factory=session_factory, authorization=KERNEL
        ),
    )
    async with context.shared.uow_factory(KERNEL) as work:
        rows = await work.evaluation_batch.results(context.scope, context.batch_id)
    row = next(row for row in rows if row["envelope"])
    run_id = UUID(str(row["run_id"]))
    accepted = await handler.handle(CommandEnvelope.model_validate(row["envelope"]))
    assertion("admitted_create_accepted", accepted.status == "accepted")
    assertion(
        "start_accepted",
        (await handler.handle(command(context, run_id, "StartRun"))).status == "accepted",
    )
    state = await state_of(context, run_id)
    async with context.shared.uow_factory(KERNEL) as work:
        model = await context.shared.inference_model_service.resolve_chat(
            context.input.bootstrap.model_id, scope=context.scope, uow=work
        )
    if (
        model.base_url.rstrip("/") != "http://acceptance-inference:8080/v1"
        or model.model_name != "acceptance-chat"
        or model.endpoint.id != context.input.bootstrap.endpoint_id
    ):
        raise RuntimeError("non-controlled provider forbidden")
    # This must equal the frozen config, not a synthetic bound or overridden policy.
    assertion("frozen_output_bound", model.max_output_tokens == 4096)
    payload = {
        "model": model.model_name,
        "messages": [{"role": "user", "content": "[acceptance:evaluation:rule-pass]"}],
        "max_completion_tokens": model.max_output_tokens,
    }
    payload_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    wanted = set()
    for index in range(2):
        identity = activity_identity(state, f"model:{index}")
        wanted.add(identity)
        result = await handler.handle(
            command(
                context,
                run_id,
                "RequestActivity",
                {
                    "activity_id": str(identity),
                    "activity_type": "model.call",
                    "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "input_ref": "acceptance-strict-bound-physical-request",
                    "input_digest": payload_digest,
                    "input_payload": payload,
                },
            )
        )
        assertion("parallel_activity_accepted", result.status == "accepted")
        context.own("activity", identity)
    store = PostgresActivityStore(session_factory=session_factory, authorization=KERNEL)
    claims = await store.claim(
        now=datetime.now(UTC),
        limit=2,
        worker_id="acceptance-strict-driver",
        claim_ttl=timedelta(minutes=5),
    )
    assertion(
        "exact_owned_activity_claims", {claim.request.activity_id for claim in claims} == wanted
    )
    contexts = []
    for claim in claims:
        assertion("claim_started", await store.mark_call_started(claim, now=datetime.now(UTC)))
        result = await handler.handle(
            command(
                context,
                run_id,
                "MarkActivityCallStarted",
                {
                    "activity_id": str(claim.request.activity_id),
                    "generation": claim.request.generation,
                    "claim_generation": claim.claim_generation,
                },
                version=2,
            )
        )
        assertion("formal_call_started", result.status == "accepted")
        contexts.append(
            ActivityContext(
                worker_id="acceptance-strict-driver",
                claim_generation=claim.claim_generation,
                idempotency_key=str(claim.request.activity_id),
                owner_user_id=context.scope.user_id,
                team_id=None,
                run=run_execution_context(await state_of(context, run_id)),
            )
        )
    dispatch = DurableBudgetDispatchService(
        uow_factory=context.shared.uow_factory,
        inventory=build_budget_authority(context.settings).inventory,
        physical_policy=configured_physical_policy(context.settings),
        execution_policy=policy,
    )
    outcomes = await asyncio.gather(
        *(
            dispatch.before_send(context.scope, claim.request, call, model, payload)
            for claim, call in zip(claims, contexts, strict=True)
        ),
        return_exceptions=True,
    )
    successes = [value for value in outcomes if not isinstance(value, BaseException)]
    failures = [value for value in outcomes if isinstance(value, BaseException)]
    assertion("one_conservative_permit", len(successes) == 1 and len(failures) == 1)
    assertion(
        "budget_exhaustion_reason",
        isinstance(failures[0], ValueError) and "budget_exhausted" in str(failures[0]),
    )
    identity = successes[0].consume()
    context.own("physical_call", identity)
    async with context.shared.uow_factory(KERNEL) as work:
        reserved = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key=:key"
                    ),
                    {"key": "5:batch:" + str(context.batch_id)},
                )
            )
            .mappings()
            .one()
        )
        assertion(
            "full_bound_reserved", reserved["reserved_tokens"] == 266240 and reserved["slots"] == 1
        )
    # One physical transport, no automatic retries, no provider outside owned stack.
    async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
        response = await client.post(
            model.base_url.rstrip("/") + "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + model.credential},
        )
        response.raise_for_status()
        raw_receipt_digest = hashlib.sha256(response.content).hexdigest()
        body = response.json()
    usage = normalize_usage(body.get("usage"), provider="openai")
    assertion(
        "actual_provider_usage",
        type(usage.get("total_tokens")) is int and usage["total_tokens"] > 0,
    )
    await dispatch.mark_unknown(context.scope, identity)
    assertion(
        "cancel_accepted",
        (
            await handler.handle(
                command(context, run_id, "CancelRun", {"reason": "acceptance delayed response"})
            )
        ).status
        == "accepted",
    )
    # The owned kernel is stopped for this scenario; apply its formal projector
    # before asking the scheduler to observe the cancelled Run.
    projector = PostgresFormalProjector(session_factory=session_factory, authorization=KERNEL)
    for _ in range(20):
        projected = await projector.run_once(context.scope, limit=100, notify=False)
        if not projected.processed:
            break
    else:
        raise RuntimeError("cancelled Run projection did not converge")
    async with context.shared.uow_factory(KERNEL) as work:
        projection = await work.evaluation_batch.projection(context.scope, row)
    assertion(
        "cancelled_run_projection_visible",
        projection is not None and projection["status"] == "cancelled",
    )
    batch_service = BatchService(
        context.scheduler.suites, preflight_factory=context.scheduler.preflight_factory
    )
    await batch_service.cancel(
        context.scope, context.principal, str(uuid4()), {"batch_id": str(context.batch_id)}
    )
    await context.scheduler.tick(datetime.now(UTC))
    cancelled = await batch_service.get(context.scope, context.principal, context.batch_id)
    assertion("batch_cancelled_before_late_settlement", cancelled.status == "cancelled")
    first = await dispatch.after_send(context.scope, identity, usage, body.get("model"))
    second = await dispatch.after_send(context.scope, identity, usage, body.get("model"))
    assertion("same_late_settlement", first == second)
    conflict = {
        **usage,
        "prompt_tokens": usage["prompt_tokens"] + 1,
        "total_tokens": usage["total_tokens"] + 1,
    }
    try:
        await dispatch.after_send(context.scope, identity, conflict, body.get("model"))
    except ValueError as error:
        assertion("conflicting_usage_refused", "conflict" in str(error))
    else:
        raise AssertionError("conflicting settlement accepted")
    async with context.shared.uow_factory(KERNEL) as work:
        count = await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_settlements WHERE call_identity=:id"),
            {"id": identity},
        )
        settled = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key=:key"
                    ),
                    {"key": "5:batch:" + str(context.batch_id)},
                )
            )
            .mappings()
            .one()
        )
    assertions = [
        assertion("one_actual_settlement", count == 1),
        assertion(
            "bound_holds_released", settled["slots"] == 0 and settled["reserved_tokens"] == 0
        ),
        assertion("scoped_spend_matches_receipt", settled["spent_tokens"] == usage["total_tokens"]),
        assertion(
            "cancelled_run_stays_terminal",
            (await state_of(context, run_id)).status.value == "cancelled",
        ),
    ]
    await context.scheduler.tick(datetime.now(UTC))
    after_batch = await batch_service.get(context.scope, context.principal, context.batch_id)
    assertions.append(
        assertion("late_settlement_does_not_revive_batch", after_batch.status == "cancelled")
    )
    resources = {
        "batch_id": str(context.batch_id),
        "run_id": str(run_id),
        "call_identity": identity,
    }
    after = {
        "provider_receipt_sha256": raw_receipt_digest,
        "usage": usage,
        "ledger": dict(settled),
        "physical_sends": 1,
    }
    context.scenarios.append(
        scenario(
            "budget_exhaustion",
            resources,
            {"reserved": dict(reserved), "attempted": 2, "denied": 1},
            after,
            assertions,
        )
    )
    late = scenario(
        "late_completion_cancel",
        resources,
        {"status": "cancelled", "settlement": "unknown"},
        after,
        assertions,
        mechanism="actual_provider_delayed_callback",
    )
    late["requirement"] = "AC12"
    context.scenarios.append(late)
    # The delayed non-idempotent effect is intentionally unknown, and E12
    # must reject archival of its parent batch. Preserve the immutable
    # evidence until the exact disposable acceptance database is removed.
    for item in context.scenarios:
        if (
            item.get("resource_ids", {}).get("batch_id") == str(context.batch_id)
            and item.get("cleanup", {}).get("state") == "pending"
        ):
            item["cleanup"] = {
                "state": "retained_immutable",
                "disposal": "exact_disposable_database",
            }
