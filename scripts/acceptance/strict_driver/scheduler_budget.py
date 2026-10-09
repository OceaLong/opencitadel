"""One real settled subject call, then the scheduler rejects the next admission.

Runs before other strict work with the owned kernel quiesced by the host bridge.
No activity/scoring consumers are started. Formal commands and physical guard are
real; only orchestration is manual to make the spend/next-dispatch boundary exact.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from strict_driver.budget import command, state_of
from strict_driver.environment import drain_lease
from strict_driver.ownership import KERNEL, assert_exclusive
from strict_driver.recovery import assertion, scenario

from app.application.evaluation.batch_service import BatchService
from app.application.execution.decisions.base import activity_identity
from app.application.execution.run_context import run_execution_context
from app.composition.evaluation import build_budget_authority, build_environment_registry
from app.composition.evaluation_execution import configured_execution_policy
from app.composition.physical_budget import configured_physical_policy
from app.domain.evaluation.configuration import SuiteDefinition
from app.domain.execution.activity import ActivityContext
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.external.llm.base_llm import normalize_usage


async def scheduler_budget_stop(context):
    scheduler, scope, principal = context.scheduler, context.scope, context.principal
    suites = scheduler.suites
    original_limit, original_batch = scheduler.dispatch_limit, context.batch_id
    registry = build_environment_registry(context.settings)
    service = BatchService(suites, preflight_factory=scheduler.preflight_factory)
    await assert_exclusive(context.shared.uow_factory, context.allowed)
    source = await suites.get_version(
        scope, principal, "suite", context.input.bootstrap.suite_version.id
    )
    config = await suites.get_version(scope, principal, "config", source.config_versions[0])
    rubric = await suites.get_version(scope, principal, "rubric", source.rubric_version)
    judge = await suites.get_version(scope, principal, "config", rubric.judge_config_version)
    bound = max(candidate["tokens"] for candidate in config.snapshot["budget"]["candidates"])
    judge_bound = max(candidate["tokens"] for candidate in judge.snapshot["budget"]["candidates"])
    assertion("subject_bound_covers_pinned_judge", bound >= judge_bound > 0)
    definition = SuiteDefinition.model_validate(
        {key: getattr(source, key) for key in SuiteDefinition.model_fields}
    )
    definition = definition.model_copy(
        update={
            "settings": source.settings.model_copy(
                update={
                    "repeat": 2,
                    "token_budget": max(bound, judge_bound),
                    "money_budget": None,
                }
            )
        }
    )
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Acceptance scheduler budget stop",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    context.own("suite", draft.id)
    published = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=draft.revision,
        request_id=str(uuid4()),
    )
    context.own("suite_version", published.id)
    check = await scheduler.preflight_factory(principal).check(scope, published.id)
    assertion("budget_bound_preflight_allowed", check.allowed)
    key = str(uuid4())
    context.own("batch_request", key)
    batch = await service.start(
        scope,
        principal,
        key,
        {"suite_version": str(published.id), "preflight_revision": check.revision},
    )
    context.own("batch", batch.id)
    context.batch_id = batch.id
    scheduler.dispatch_limit = 1
    try:
        # Initial isolated allocation needs real prepare/reset/verify-ready before
        # one admission. Stop immediately on the first envelope, before next tick.
        for _ in range(12):
            await assert_exclusive(context.shared.uow_factory, context.allowed)
            await scheduler.tick(datetime.now(UTC))
            await context.register_batch_children()
            async with context.shared.uow_factory(KERNEL) as work:
                rows = await work.evaluation_batch.results(scope, batch.id)
                leases = await work.evaluation_batch.environment_leases(scope, batch.id)
            admitted = [row for row in rows if row["envelope"]]
            if admitted:
                break
            for lease in leases:
                if lease["state"] not in {"ready", "leased", "verified_clean"}:
                    await drain_lease(context, lease["id"], registry)
        else:
            raise AssertionError("first bounded admission did not converge")
        assertion("exactly_two_slots_one_admission", len(rows) == 2 and len(admitted) == 1)
        first = admitted[0]
        second = next(row for row in rows if row["id"] != first["id"])
        run_id = UUID(str(first["run_id"]))
        policy = configured_execution_policy(context.settings)
        factory = context.resources.postgres.session_factory
        handler = SqlAlchemyExecutionOrchestrator(
            session_factory=factory,
            aggregates={"run": RunAggregate()},
            authorization=KERNEL,
            evaluation_execution=EvaluationExecutionGuard(
                policy, session_factory=factory, authorization=KERNEL
            ),
        )
        assertion(
            "budget_create_accepted",
            (await handler.handle(CommandEnvelope.model_validate(first["envelope"]))).status
            == "accepted",
        )
        assertion(
            "budget_start_accepted",
            (await handler.handle(command(context, run_id, "StartRun"))).status == "accepted",
        )
        async with context.shared.uow_factory(KERNEL) as work:
            model = await context.shared.inference_model_service.resolve_chat(
                context.input.bootstrap.model_id, scope=scope, uow=work
            )
        if (
            model.base_url.rstrip("/") != "http://acceptance-inference:8080/v1"
            or model.model_name != "acceptance-chat"
            or model.endpoint.id != context.input.bootstrap.endpoint_id
        ):
            raise RuntimeError("non-controlled provider forbidden")
        assertion(
            "published_output_matches_physical_call",
            model.max_output_tokens == config.selection.max_output_tokens,
        )
        payload = {
            "model": model.model_name,
            "messages": [{"role": "user", "content": "[acceptance:evaluation:rule-pass]"}],
            "max_completion_tokens": model.max_output_tokens,
        }
        activity = activity_identity(await state_of(context, run_id), "model:0")
        assertion(
            "budget_request_persisted",
            (
                await handler.handle(
                    command(
                        context,
                        run_id,
                        "RequestActivity",
                        {
                            "activity_id": str(activity),
                            "activity_type": "model.call",
                            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                            "input_ref": "acceptance-scheduler-budget-request",
                            "input_digest": hashlib.sha256(
                                json.dumps(payload, sort_keys=True).encode()
                            ).hexdigest(),
                            "input_payload": payload,
                        },
                    )
                )
            ).status
            == "accepted",
        )
        context.own("activity", activity)
        await assert_exclusive(context.shared.uow_factory, context.allowed)
        store = PostgresActivityStore(session_factory=factory, authorization=KERNEL)
        claims = await store.claim(
            now=datetime.now(UTC),
            limit=1,
            worker_id="acceptance-scheduler-budget",
            claim_ttl=timedelta(minutes=5),
        )
        assertion(
            "exact_budget_activity_claim",
            len(claims) == 1 and claims[0].request.activity_id == activity,
        )
        claim = claims[0]
        assertion(
            "budget_call_started", await store.mark_call_started(claim, now=datetime.now(UTC))
        )
        assertion(
            "budget_formal_call_started",
            (
                await handler.handle(
                    command(
                        context,
                        run_id,
                        "MarkActivityCallStarted",
                        {
                            "activity_id": str(activity),
                            "generation": claim.request.generation,
                            "claim_generation": claim.claim_generation,
                        },
                        version=2,
                    )
                )
            ).status
            == "accepted",
        )
        call = ActivityContext(
            worker_id="acceptance-scheduler-budget",
            claim_generation=claim.claim_generation,
            idempotency_key=str(activity),
            owner_user_id=scope.user_id,
            team_id=scope.team_id,
            run=run_execution_context(await state_of(context, run_id)),
        )
        dispatch = DurableBudgetDispatchService(
            uow_factory=context.shared.uow_factory,
            inventory=build_budget_authority(context.settings).inventory,
            physical_policy=configured_physical_policy(context.settings),
            execution_policy=policy,
        )
        permit = await dispatch.before_send(scope, claim.request, call, model, payload)
        identity = permit.consume()
        context.own("physical_call", identity)
        async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
            response = await client.post(
                model.base_url.rstrip("/") + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + model.credential},
            )
            response.raise_for_status()
            body = response.json()
        usage = normalize_usage(body.get("usage"), provider="openai")
        assertion(
            "positive_real_usage_within_bound",
            type(usage.get("total_tokens")) is int and 0 < usage["total_tokens"] <= bound,
        )
        await dispatch.after_send(scope, identity, usage, body.get("model"))
        assertion(
            "budget_activity_completed",
            (
                await handler.handle(
                    command(
                        context,
                        run_id,
                        "CompleteActivity",
                        {
                            "activity_id": str(activity),
                            "generation": claim.request.generation,
                            "claim_generation": claim.claim_generation,
                            "result_summary": body["choices"][0]["message"]["content"],
                        },
                        version=2,
                    )
                )
            ).status
            == "accepted",
        )
        assertion(
            "budget_run_completed",
            (await handler.handle(command(context, run_id, "CompleteRun"))).status == "accepted",
        )
        assertion(
            "persisted_subject_run_completed",
            (await state_of(context, run_id)).status.value == "completed",
        )
        # The owned kernel is quiesced during this strict scenario. Advance the
        # same formal projector that normally follows committed Run events so
        # the scheduler can reconcile the actual terminal projection.
        projector = PostgresFormalProjector(session_factory=factory, authorization=KERNEL)
        for _ in range(20):
            projected = await projector.run_once(scope, limit=100, notify=False)
            if not projected.processed:
                break
        else:
            raise RuntimeError("budget Run projection did not converge")
        async with context.shared.uow_factory(KERNEL) as work:
            projection = await work.evaluation_batch.projection(scope, first)
        assertion(
            "budget_run_projection_completed",
            projection is not None and projection["status"] == "completed",
        )
        # No scoring worker runs here: the mandatory pinned judge bound was
        # included in preflight, and no judge dispatch can race this boundary.
        for _ in range(4):
            await assert_exclusive(context.shared.uow_factory, context.allowed)
            await scheduler.tick(datetime.now(UTC))
            async with context.shared.uow_factory(KERNEL) as work:
                rows = await work.evaluation_batch.results(scope, batch.id)
            blocked = next(row for row in rows if row["id"] == second["id"])
            if blocked["execution_status"] == "blocked_budget":
                break
        async with context.shared.uow_factory(KERNEL) as work:
            receipt = await work.evaluation_batch.receipt(scope, blocked)
            ledger = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key=:key"
                        ),
                        {"key": "5:batch:" + str(batch.id)},
                    )
                )
                .mappings()
                .one()
            )
            settlements = await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_settlements WHERE call_identity=:id"),
                {"id": identity},
            )
            active = await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_activity_tasks WHERE run_id=:run AND status IN ('pending','claimed','call_started')"
                ),
                {"run": str(run_id)},
            )
        assertions = [
            assertion(
                "next_scheduler_slot_blocked_without_admission",
                blocked["execution_status"] == "blocked_budget"
                and blocked["scoring_status"] == "skipped"
                and not blocked["envelope"]
                and not blocked["prepared_envelope"]
                and receipt is None,
            ),
            assertion(
                "one_settlement_positive_spend_zero_occupancy",
                settlements == 1
                and ledger["spent_tokens"] == usage["total_tokens"]
                and ledger["slots"] == 0
                and ledger["reserved_tokens"] == 0,
            ),
            assertion("formal_activity_converged", active == 0),
        ]
        # Cancel only after observing real blocked_budget. This ends pending
        # scoring; it is not evidence that the whole batch ended due to budget.
        await service.cancel(scope, principal, str(uuid4()), {"batch_id": str(batch.id)})
        tick_claims = []
        for _ in range(8):
            await assert_exclusive(context.shared.uow_factory, context.allowed)
            tick_claims.append((await scheduler.tick(datetime.now(UTC))).claimed)
            async with context.shared.uow_factory(KERNEL) as work:
                leases = await work.evaluation_batch.environment_leases(scope, batch.id)
            for lease in leases:
                if lease["state"] != "verified_clean":
                    await drain_lease(context, lease["id"], registry)
            current = await service.get(scope, principal, batch.id)
            if current.status == "cancelled" and current.cleanup_status == "clean":
                break
        if current.status != "cancelled" or current.cleanup_status != "clean":
            # These are schema-validated enum values, safe to retain in the
            # strict driver's sanitized failure receipt.
            async with context.shared.uow_factory(KERNEL) as work:
                final_rows = await work.evaluation_batch.results(scope, batch.id)
                final_leases = await work.evaluation_batch.environment_leases(scope, batch.id)
                final_receipts = [
                    await work.evaluation_batch.receipt(scope, row) for row in final_rows
                ]
                final_projections = [
                    await work.evaluation_batch.projection(scope, row) for row in final_rows
                ]
            context.report["failure_state"] = {
                "batch_status": current.status,
                "cleanup_status": current.cleanup_status,
                "result_execution": [row["execution_status"] for row in final_rows],
                "result_scoring": [row["scoring_status"] for row in final_rows],
                "lease_states": [lease["state"] for lease in final_leases],
                "receipt_status": [
                    receipt["status"] if receipt else None for receipt in final_receipts
                ],
                "projection_status": [
                    projection["status"] if projection else None for projection in final_projections
                ],
                "projection_version": [
                    projection["stream_version"] if projection else None
                    for projection in final_projections
                ],
                "result_run_revision": [row["run_revision"] for row in final_rows],
                "tick_claims": tick_claims,
            }
            raise AssertionError(f"budget_cleanup_{current.status}_{current.cleanup_status}")
        assertions.append(
            assertion(
                "new_budget_batch_cancelled_and_cleaned_after_observation",
                current.status == "cancelled" and current.cleanup_status == "clean",
            )
        )
        public = await service.results(scope, principal, batch.id)
        retained = next(row for row in public["items"] if row.id == second["id"])
        assertions.append(
            assertion(
                "budget_reason_retained_after_cleanup",
                retained.execution_status == "blocked_budget"
                and retained.scoring_status == "skipped",
            )
        )
        item = scenario(
            "scheduler_budget_stop",
            {
                "batch_id": str(batch.id),
                "suite_version": str(published.id),
                "first_result_id": str(first["id"]),
                "blocked_result_id": str(second["id"]),
                "run_id": str(run_id),
                "activity_id": str(activity),
                "call_identity": identity,
            },
            {
                "preflight_allowed": check.allowed,
                "preflight_revision": check.revision,
                "subject_bound": bound,
                "judge_bound": judge_bound,
                "token_budget": published.settings.token_budget,
                "repeat": published.settings.repeat,
                "dispatch_limit": 1,
            },
            {
                "usage": usage,
                "ledger": dict(ledger),
                "settlements": settlements,
                "physical_sends": 1,
                "provider_receipt_sha256": hashlib.sha256(response.content).hexdigest(),
                "execution_status": blocked["execution_status"],
                "scoring_status": blocked["scoring_status"],
                "envelope": bool(blocked["envelope"]),
                "prepared_envelope": bool(blocked["prepared_envelope"]),
                "admission_receipt": receipt is not None,
                "cleanup_batch_status": current.status,
            },
            assertions,
            mechanism="real_scheduler_after_positive_physical_settlement",
        )
        item["cleanup"] = {
            "state": "verified_clean",
            "batch_status": current.status,
            "obligations": ["archive retained batch and suite history"],
        }
        from strict_bridge import validate_scheduler_budget

        validate_scheduler_budget(item)
        context.scenarios.append(item)
    finally:
        scheduler.dispatch_limit = original_limit
        context.batch_id = original_batch
