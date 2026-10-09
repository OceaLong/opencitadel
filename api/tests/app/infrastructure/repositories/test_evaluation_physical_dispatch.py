# ruff: noqa: F401,F811
"""D1 composes real F07 receipt allocation with bound budgets in one pool-one UoW."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_execution_slots import (
    bound_run,
    run_command,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def ready_dispatch(budget_binding_fixture, monkeypatch):
    from app.application.execution.decisions.base import activity_identity
    from app.application.execution.run_context import run_execution_context
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.run import RunAggregate, RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
        DBEvaluationBudgetPolicyRepository,
    )
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    execution_policy = ExecutionSlotPolicy(revision=1)
    physical_policy = BudgetPolicy(
        revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
    )
    auth = AuthorizationContext.system("execution-kernel")
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(execution_policy, session_factory=factory, authorization=auth)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=auth,
        evaluation_execution=guard,
    )
    async with uow(auth) as work:
        await work.evaluation_execution.prepare(scope, binding.run_id, execution_policy)
        await DBEvaluationBudgetPolicyRepository(work.db_session).bootstrap(physical_policy)
        await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(run_command(fixture, binding, snapshot, kind))
        ).status == "accepted"
    async with uow(auth) as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": binding.run_id},
            )
        )
    activity_id = activity_identity(state, "model:0")
    assert (
        await handler.handle(
            run_command(
                fixture,
                binding,
                snapshot,
                "RequestActivity",
                {
                    "activity_id": str(activity_id),
                    "activity_type": "model.call",
                    "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "input_ref": "test",
                    "input_digest": "test",
                },
            )
        )
    ).status == "accepted"
    store = PostgresActivityStore(session_factory=factory, authorization=auth)
    claim = (
        await store.claim(
            now=datetime.now(UTC), limit=1, worker_id="d1", claim_ttl=timedelta(minutes=5)
        )
    )[0]
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    assert (
        await handler.handle(
            run_command(
                fixture,
                binding,
                snapshot,
                "MarkActivityCallStarted",
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "claim_generation": claim.claim_generation,
                },
            ).model_copy(update={"command_schema_version": 2})
        )
    ).status == "accepted"
    async with uow(auth) as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": binding.run_id},
            )
        )
    context = ActivityContext(
        worker_id="d1",
        claim_generation=claim.claim_generation,
        idempotency_key=str(activity_id),
        owner_user_id=scope.user_id,
        team_id=None,
        run=run_execution_context(state),
    )
    monkeypatch.setattr(
        ApiKeyCipher, "decrypt_versioned", lambda self, value: "fake-provider-credential"
    )
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter

    models = InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None)
    model = await models.resolve_chat("e02-model", scope=scope)
    payload = {
        "model": model.model_name,
        "messages": [{"role": "user", "content": "hello"}],
        "max_completion_tokens": 8192,
    }
    return (
        fixture,
        binding,
        claim.request,
        context,
        model,
        payload,
        physical_policy,
        execution_policy,
        handler,
    )


async def test_failed_reservation_rolls_back_f07_allocation_and_only_committed_permit_can_send(
    ready_dispatch,
):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    fixture, binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    first = await dispatch.before_send(scope, request, context, model, payload)
    second = await dispatch.before_send(scope, request, context, model, payload)
    assert first.consume() != second.consume()
    with pytest.raises(ValueError, match="already_dispatched"):
        first.consume()
    with pytest.raises(ValueError, match="budget_exhausted"):
        await dispatch.before_send(scope, request, context, model, payload)
    async with uow(dispatch.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 2
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 2
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 2
        )


async def test_unknown_late_usage_settles_f07_and_budget_once_after_cancel_and_revocation(
    ready_dispatch,
):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.base_llm import normalize_usage
    from tests.app.execution_test_support import execution_admin_session

    (
        fixture,
        binding,
        request,
        context,
        model,
        payload,
        physical_policy,
        execution_policy,
        handler,
    ) = ready_dispatch
    service, scope, principal, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    await dispatch.mark_unknown(scope, identity)
    assert (
        await handler.handle(
            run_command(
                fixture, binding, context.run.policy_snapshot, "CancelRun", {"reason": "timeout"}
            )
        )
    ).status == "accepted"
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    usage = normalize_usage(
        {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}, provider="openai"
    )
    first = await dispatch.after_send(scope, identity, usage, model.model_name)
    assert await dispatch.after_send(scope, identity, usage, model.model_name) == first
    with pytest.raises(ValueError, match=r"conflicting|conflict"):
        await dispatch.after_send(
            scope,
            identity,
            normalize_usage(
                {"prompt_tokens": 13, "completion_tokens": 3, "total_tokens": 16}, provider="openai"
            ),
            model.model_name,
        )
    async with uow(dispatch.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_settlements WHERE call_identity=:id"),
                {"id": identity},
            )
            == 1
        )
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert dict(row) == {"slots": 0, "reserved_tokens": 0, "spent_tokens": 15}
        assert (
            await work.db_session.scalar(
                text("SELECT state->>'status' FROM evaluation_execution_leases WHERE run_id=:id"),
                {"id": binding.run_id},
            )
            == "cancelled"
        )


async def test_partial_final_fact_preserves_unknown_token_hold_and_rejects_enrichment(
    ready_dispatch,
):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    fixture, _binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    await dispatch.after_send(
        scope,
        identity,
        {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        None,
    )
    async with uow(dispatch.authorization) as work:
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["slots"] == 0
        assert row["reserved_tokens"] > 1000000
        assert row["spent_tokens"] == 0
    with pytest.raises(ValueError, match=r"conflicting|conflict"):
        await dispatch.after_send(
            scope, identity, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, None
        )


async def test_logical_cap_survives_reclaimed_worker_claim_and_keeps_all_receipts(ready_dispatch):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.external.llm.base_llm import normalize_usage
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_admin_session,
    )

    (
        fixture,
        binding,
        request,
        context,
        model,
        payload,
        physical_policy,
        execution_policy,
        handler,
    ) = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    empty = normalize_usage(
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, provider="openai"
    )
    for _ in range(2):
        identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
        await dispatch.after_send(scope, identity, empty, model.model_name)
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()-INTERVAL '1 second' WHERE activity_id=:id"
            ),
            {"id": request.activity_id},
        )
        await db.commit()
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    store = PostgresActivityStore(session_factory=factory, authorization=dispatch.authorization)
    claim = (
        await store.claim(
            now=datetime.now(UTC), limit=1, worker_id="d1-recovered", claim_ttl=timedelta(minutes=5)
        )
    )[0]
    assert claim.recovered_after_call_started
    assert claim.claim_generation == context.claim_generation + 1
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    command = run_command(
        fixture,
        binding,
        context.run.policy_snapshot,
        "MarkActivityCallStarted",
        {
            "activity_id": str(request.activity_id),
            "generation": 0,
            "claim_generation": claim.claim_generation,
        },
    ).model_copy(update={"command_schema_version": 2})
    assert (await handler.handle(command)).status == "accepted"
    recovered = context.model_copy(update={"claim_generation": claim.claim_generation})
    identity = (await dispatch.before_send(scope, request, recovered, model, payload)).consume()
    await dispatch.after_send(scope, identity, empty, model.model_name)
    with pytest.raises(ValueError, match="budget_logical_attempts_exhausted"):
        await dispatch.before_send(scope, request, recovered, model, payload)
    async with uow(dispatch.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 3
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(DISTINCT attempt_id) FROM execution_model_dispatches WHERE run_id=:run"
                ),
                {"run": binding.run_id},
            )
            == 2
        )
        assert (
            await work.db_session.scalar(
                text("SELECT sends FROM evaluation_model_logical_calls WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 3
        )


async def test_commit_failure_never_exposes_permit_and_rolls_back_f07_budget_and_lineage(
    ready_dispatch, monkeypatch
):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.repositories.db_uow import DBUnitOfWork

    fixture, binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )

    async def fail_commit(work):
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(DBUnitOfWork, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected"):
        await dispatch.before_send(scope, request, context, model, payload)
    async with uow(dispatch.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_model_logical_calls")
            )
            == 0
        )


async def test_actual_bound_breach_keeps_full_f07_usage_and_blocks_next_reservation(ready_dispatch):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.base_llm import normalize_usage

    fixture, _binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    usage = normalize_usage(
        {"prompt_tokens": 2000000, "completion_tokens": 3, "total_tokens": 2000003},
        provider="openai",
    )
    await dispatch.after_send(scope, identity, usage, model.model_name)
    async with uow(dispatch.authorization) as work:
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT fact->'usage'->>'total_tokens' FROM execution_model_settlements WHERE call_identity=:id"
                ),
                {"id": identity},
            )
            == "2000003"
        )
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT spent_tokens,reserved_tokens,breached FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert dict(row) == {"spent_tokens": 2000003, "reserved_tokens": 0, "breached": True}
    with pytest.raises(ValueError, match="budget_bound_breached"):
        await dispatch.before_send(scope, request, context, model, payload)


@pytest.mark.parametrize(
    "budget_binding_fixture",
    [
        {
            "price": {
                "input_per_million": "1",
                "output_per_million": "2",
                "cache_read_per_million": "0.1",
                "cache_write_per_million": "1",
                "reasoning_uses_output_rate": True,
            },
            "money_budget": 5.0,
        }
    ],
    indirect=True,
)
async def test_fixed_money_and_token_coverage_settle_independently_from_remote_completion(
    ready_dispatch,
):
    from decimal import Decimal

    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.base_llm import normalize_usage

    fixture, _binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    first = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    complete = normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
        provider="openai",
    )
    fact = await dispatch.after_send(scope, first, complete, model.model_name)
    assert Decimal(fact["cost_usd"]) == Decimal("0.000014")
    assert fact["version_unpinned"] is False
    second = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    partial = normalize_usage(
        {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, provider="openai"
    )
    assert (await dispatch.after_send(scope, second, partial, model.model_name))["cost_usd"] is None
    third = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    await dispatch.mark_unknown(scope, third)
    async with uow(dispatch.authorization) as work:
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,reserved_money,spent_tokens,spent_money FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["slots"] == 1  # only the timed-out, remotely uncertain request
        assert row["spent_tokens"] == 24
        assert row["spent_money"] == Decimal("0.000014")
        assert row["reserved_tokens"] > 1000000  # timeout only
        assert row["reserved_money"] > Decimal(2)  # partial final plus timeout


async def replacement_binding(ready):
    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.budget_binding import BudgetBindingSelection

    fixture, original, _, context, _, _, _, _, _ = ready
    service, scope, principal, suite, _, pair, uow = fixture
    run_id, source_id = uuid4(), str(uuid4())
    async with uow() as work:
        await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=original.namespace_id,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        await prepare_replay_binding(
            work,
            service,
            scope,
            principal,
            run_id=run_id,
            source_entity_id=source_id,
            config_version_id=original.config_version_id,
            recording_version_id=suite.recording_versions[0],
            policy_pair=pair,
            policy_snapshot=context.run.policy_snapshot,
        )
        selection = BudgetBindingSelection(
            namespace_id=original.namespace_id,
            run_id=run_id,
            source_entity_id=source_id,
            case_id=original.case_id,
            config_version_id=original.config_version_id,
            subject_config_version_id=original.subject_config_version_id,
            repeat=original.repeat,
        )
        result = await prepare_budget_binding(
            work,
            service,
            scope,
            principal,
            selection=selection,
            policy_pair=pair,
            policy_snapshot=context.run.policy_snapshot,
        )
        await work.commit()
    return result


async def test_second_run_for_same_work_unit_requires_explicit_predecessor_and_accepted_retry_state(
    ready_dispatch,
):
    fixture, original, request, context, _, _, _, policy, handler = ready_dispatch
    _, scope, _, _, _, _, uow = fixture
    replacement = await replacement_binding(ready_dispatch)
    async with uow() as work:
        with pytest.raises(ValueError, match="budget_lineage_predecessor_required"):
            await work.evaluation_execution.prepare(scope, replacement.run_id, policy)
    async with uow() as work:
        with pytest.raises(ValueError, match="budget_lineage_predecessor_state"):
            await work.evaluation_lineage.link_replacement(
                scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=1
            )
    failed = run_command(
        fixture,
        original,
        context.run.policy_snapshot,
        "FailActivity",
        {
            "activity_id": str(request.activity_id),
            "generation": 0,
            "claim_generation": context.claim_generation,
            "failure_code": "PROVIDER_UNAVAILABLE",
        },
    ).model_copy(update={"command_schema_version": 2})
    assert (await handler.handle(failed)).status == "accepted"
    assert (
        await handler.handle(
            run_command(
                fixture,
                original,
                context.run.policy_snapshot,
                "FailRun",
                {"failure_code": "PROVIDER_UNAVAILABLE", "retryable": True},
            )
        )
    ).status == "accepted"
    async with uow() as work:
        with pytest.raises(ValueError, match="budget_lineage_generation_stale"):
            await work.evaluation_lineage.link_replacement(
                scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=1
            )
    async with uow() as work:
        lineage = await work.evaluation_lineage.link_replacement(
            scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=2
        )
        assert str(lineage["root_run_id"]) == str(original.run_id)
        assert (
            await work.evaluation_lineage.link_replacement(
                scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=2
            )
            == lineage
        )
        await work.evaluation_execution.prepare(scope, replacement.run_id, policy)
        await work.commit()
    assert (
        await handler.handle(
            run_command(fixture, original, context.run.policy_snapshot, "RetryRun")
        )
    ).status == "deferred"


async def test_replacement_under_original_scoped_authority_cannot_ignore_unknown_physical_effect(
    ready_dispatch,
):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    (
        fixture,
        original,
        request,
        context,
        model,
        payload,
        physical_policy,
        execution_policy,
        handler,
    ) = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
    await dispatch.mark_unknown(scope, identity)
    replacement = await replacement_binding(ready_dispatch)
    failed = run_command(
        fixture,
        original,
        context.run.policy_snapshot,
        "FailActivity",
        {
            "activity_id": str(request.activity_id),
            "generation": 0,
            "claim_generation": context.claim_generation,
            "failure_code": "PROVIDER_UNAVAILABLE",
        },
    ).model_copy(update={"command_schema_version": 2})
    assert (await handler.handle(failed)).status == "accepted"
    assert (
        await handler.handle(
            run_command(
                fixture,
                original,
                context.run.policy_snapshot,
                "FailRun",
                {"failure_code": "PROVIDER_UNAVAILABLE", "retryable": True},
            )
        )
    ).status == "accepted"
    async with uow() as work:
        with pytest.raises(ValueError, match="budget_lineage_unknown_effect"):
            await work.evaluation_lineage.link_replacement(
                scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=2
            )


async def test_replacement_inherits_exhausted_physical_round_counter(ready_dispatch):
    from app.application.execution.decisions.base import activity_identity
    from app.application.execution.run_context import run_execution_context
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.run import RunState
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.external.llm.base_llm import normalize_usage

    (
        fixture,
        original,
        request,
        context,
        model,
        payload,
        physical_policy,
        execution_policy,
        handler,
    ) = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    for _ in range(3):
        identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()
        await dispatch.after_send(
            scope,
            identity,
            normalize_usage(
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, provider="openai"
            ),
            model.model_name,
        )
    replacement = await replacement_binding(ready_dispatch)
    failed = run_command(
        fixture,
        original,
        context.run.policy_snapshot,
        "FailActivity",
        {
            "activity_id": str(request.activity_id),
            "generation": 0,
            "claim_generation": context.claim_generation,
            "failure_code": "PROVIDER_UNAVAILABLE",
        },
    ).model_copy(update={"command_schema_version": 2})
    assert (await handler.handle(failed)).status == "accepted"
    assert (
        await handler.handle(
            run_command(
                fixture,
                original,
                context.run.policy_snapshot,
                "FailRun",
                {"failure_code": "PROVIDER_UNAVAILABLE", "retryable": True},
            )
        )
    ).status == "accepted"
    async with uow() as work:
        await work.evaluation_lineage.link_replacement(
            scope, replacement.run_id, predecessor_run_id=original.run_id, expected_generation=2
        )
        await work.evaluation_execution.prepare(scope, replacement.run_id, execution_policy)
        await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(
                run_command(fixture, replacement, context.run.policy_snapshot, kind)
            )
        ).status == "accepted"
    async with uow() as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": replacement.run_id},
            )
        )
    activity_id = activity_identity(state, "model:0")
    assert (
        await handler.handle(
            run_command(
                fixture,
                replacement,
                context.run.policy_snapshot,
                "RequestActivity",
                {
                    "activity_id": str(activity_id),
                    "activity_type": "model.call",
                    "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "input_ref": "test",
                    "input_digest": "test",
                },
            )
        )
    ).status == "accepted"
    store = PostgresActivityStore(
        session_factory=handler._session_factory, authorization=dispatch.authorization
    )
    claim = next(
        c
        for c in await store.claim(
            now=datetime.now(UTC), limit=10, worker_id="replacement", claim_ttl=timedelta(minutes=5)
        )
        if c.request.activity_id == activity_id
    )
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    assert (
        await handler.handle(
            run_command(
                fixture,
                replacement,
                context.run.policy_snapshot,
                "MarkActivityCallStarted",
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "claim_generation": claim.claim_generation,
                },
            ).model_copy(update={"command_schema_version": 2})
        )
    ).status == "accepted"
    async with uow() as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": replacement.run_id},
            )
        )
    next_context = ActivityContext(
        worker_id="replacement",
        claim_generation=claim.claim_generation,
        idempotency_key=str(activity_id),
        owner_user_id=scope.user_id,
        team_id=None,
        run=run_execution_context(state),
    )
    with pytest.raises(ValueError, match="budget_logical_attempts_exhausted"):
        await dispatch.before_send(scope, claim.request, next_context, model, payload)
    async with uow() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": replacement.run_id},
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT sends FROM evaluation_model_logical_calls WHERE run_id=:run"),
                {"run": original.run_id},
            )
            == 3
        )


async def test_f07_record_failure_rolls_back_budget_settlement(ready_dispatch, monkeypatch):
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.base_llm import normalize_usage
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    fixture, _, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    identity = (await dispatch.before_send(scope, request, context, model, payload)).consume()

    async def reject_record(*args, **kwargs):
        raise RuntimeError("injected_f07_record_failure")

    monkeypatch.setattr(DBExecutionUsageRepository, "record", reject_record)
    with pytest.raises(RuntimeError, match="injected_f07_record_failure"):
        await dispatch.after_send(
            scope,
            identity,
            normalize_usage(
                {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}, provider="openai"
            ),
            model.model_name,
        )
    async with uow(dispatch.authorization) as work:
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["slots"] == 1
        assert row["reserved_tokens"] > 1000000
        assert row["spent_tokens"] == 0
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_settlements WHERE call_identity=:id"),
                {"id": identity},
            )
            == 0
        )


async def test_two_workers_reserve_remaining_batch_capacity_atomically(ready_dispatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.repositories.db_uow import DBUnitOfWork

    fixture, binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, _, _, _, _, uow = fixture
    dispatch = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    await dispatch.before_send(scope, request, context, model, payload)
    engine = create_async_engine(uow().session_factory.kw["bind"].url, pool_size=1, max_overflow=0)
    try:
        # Independent connection pool models another worker, not process-local serialization.
        def other_uow(authorization=None):
            original = uow(authorization)
            original.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            return original

        other = DurableBudgetDispatchService(
            uow_factory=other_uow,
            inventory=service.budgets.inventory,
            physical_policy=physical_policy,
            execution_policy=execution_policy,
        )
        results = await asyncio.gather(
            dispatch.before_send(scope, request, context, model, payload),
            other.before_send(scope, request, context, model, payload),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, BaseException) for result in results) == 1
        errors = [result for result in results if isinstance(result, BaseException)]
        assert len(errors) == 1
        assert "budget_exhausted" in str(errors[0])
        async with uow(dispatch.authorization) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                    {"run": binding.run_id},
                )
                == 2
            )
            assert (
                await work.db_session.scalar(
                    text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                )
                == 2
            )
    finally:
        await engine.dispose()


async def test_replacement_rejects_untyped_generation_fence(ready_dispatch):
    fixture, original, _, _, _, _, _, _, _ = ready_dispatch
    _, scope, _, _, _, _, uow = fixture
    replacement = await replacement_binding(ready_dispatch)
    for generation in (True, 0, -1, "1"):
        async with uow() as work:
            with pytest.raises(ValueError, match="budget_lineage_generation_invalid"):
                await work.evaluation_lineage.link_replacement(
                    scope,
                    replacement.run_id,
                    predecessor_run_id=original.run_id,
                    expected_generation=generation,
                )


async def test_physical_policy_activation_preserves_existing_bound_namespace_and_candidate_proof(
    ready_dispatch,
):
    from app.application.evaluation.budget_admission import prepare_budget_namespace
    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.budget import BudgetPolicy
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.base_llm import normalize_usage

    fixture, binding, request, context, model, payload, physical_policy, execution_policy, _ = (
        ready_dispatch
    )
    service, scope, principal, suite, _, pair, uow = fixture
    old = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical_policy,
        execution_policy=execution_policy,
    )
    first = (await old.before_send(scope, request, context, model, payload)).consume()
    before = await PreflightService(service, principal).check(scope, suite.id)
    async with uow(old.authorization) as work:
        original_namespace = await work.evaluation_budget_control.namespace(
            scope, binding.namespace_id
        )
        original_binding = await work.evaluation_budget_control.binding(scope, binding.run_id)
        next_policy = BudgetPolicy(
            revision=2, global_concurrency=1, user_concurrency=1, provider_concurrency=1
        )
        await work.evaluation_physical_policy.activate(next_policy, expected_revision=1)
        await work.commit()
    after = await PreflightService(service, principal).check(scope, suite.id)
    assert before.evidence["budget"] != after.evidence["budget"]
    async with uow() as work:
        assert (
            await prepare_budget_namespace(
                work,
                service,
                scope,
                principal,
                namespace_id=binding.namespace_id,
                suite_version_id=suite.id,
                policy_pair=pair,
            )
            == original_namespace
        )
        assert (
            await work.evaluation_budget_control.binding(scope, binding.run_id) == original_binding
        )
        await work.commit()
    current = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=next_policy,
        execution_policy=execution_policy,
    )
    with pytest.raises(ValueError, match="budget_concurrency_exhausted"):
        await current.before_send(scope, request, context, model, payload)
    await current.after_send(
        scope,
        first,
        normalize_usage(
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, provider="openai"
        ),
        model.model_name,
    )
    assert (await current.before_send(scope, request, context, model, payload)).consume() != first
