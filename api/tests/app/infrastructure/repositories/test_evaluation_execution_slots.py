# ruff: noqa: F401,F811
"""C2 execution occupancy from actual published/bound evaluation Runs."""

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

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def bound_run(fixture, *, purpose="evaluation_subject", namespace_id=None):
    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    service, scope, principal, suite, config, pair, uow = fixture
    async with uow() as work:
        namespace = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=namespace_id or uuid4(),
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        config_id = config.id if purpose == "evaluation_subject" else namespace.judge_config_version
        snapshot = derive_run_policy_snapshot(
            pair.execution, RunFamily.AGENT if purpose == "evaluation_subject" else RunFamily.ASK
        )
        run_id, source_id = uuid4(), str(uuid4())
        if purpose == "evaluation_subject":
            await prepare_replay_binding(
                work,
                service,
                scope,
                principal,
                run_id=run_id,
                source_entity_id=source_id,
                config_version_id=config_id,
                recording_version_id=suite.recording_versions[0],
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
        selection = BudgetBindingSelection(
            namespace_id=namespace.id,
            run_id=run_id,
            source_entity_id=source_id,
            case_id=namespace.case_ids[0],
            config_version_id=config_id,
            subject_config_version_id=config.id,
            repeat=1,
        )
        binding = await prepare_budget_binding(
            work,
            service,
            scope,
            principal,
            selection=selection,
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        await work.commit()
        return binding, snapshot


async def test_durable_default_execution_pools_allow_five_subjects_and_two_judges(
    budget_binding_fixture,
):
    from app.domain.evaluation.execution_slots import (
        ExecutionCapacityUnavailable,
        ExecutionSlotPolicy,
    )
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )

    _, scope, _, _, _, _, uow = budget_binding_fixture
    policy = ExecutionSlotPolicy(revision=1)
    bindings = [
        await bound_run(budget_binding_fixture, purpose=purpose)
        for purpose in ["evaluation_subject"] * 6 + ["evaluation_judge"] * 3
    ]
    for index, (binding, _) in enumerate(bindings):
        async with uow() as work:
            repo = DBEvaluationExecutionRepository(work.db_session)
            if index in (5, 8):
                with pytest.raises(ExecutionCapacityUnavailable):
                    await repo.prepare(scope, binding.run_id, policy)
            else:
                lease = await repo.prepare(scope, binding.run_id, policy)
                assert lease["phase"] == "prepared"
                assert await repo.prepare(scope, binding.run_id, policy) == lease
                await work.commit()
    async with uow() as work:
        rows = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT occupied FROM evaluation_execution_pools WHERE key LIKE '2:%' ORDER BY key"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(rows) == [2, 5]


async def test_execution_policy_activation_preserves_prepared_holds_and_rejects_stale_restart(
    budget_binding_fixture,
):
    from app.domain.evaluation.execution_slots import (
        ExecutionCapacityUnavailable,
        ExecutionSlotPolicy,
    )
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )

    _, scope, _, _, _, _, uow = budget_binding_fixture
    first, _ = await bound_run(budget_binding_fixture)
    second, _ = await bound_run(budget_binding_fixture)
    third, _ = await bound_run(budget_binding_fixture)
    original = ExecutionSlotPolicy(revision=1, global_limit=2)
    async with uow() as work:
        repo = DBEvaluationExecutionRepository(work.db_session)
        await repo.prepare(scope, first.run_id, original)
        await repo.prepare(scope, second.run_id, original)
        await work.commit()
    reduced = ExecutionSlotPolicy(revision=2, global_limit=1)
    async with uow() as work:
        repo = DBEvaluationExecutionRepository(work.db_session)
        await repo.activate(reduced, expected_revision=1)
        assert (
            await work.db_session.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
            )
            == 2
        )
        await work.commit()
    async with uow() as work:
        repo = DBEvaluationExecutionRepository(work.db_session)
        with pytest.raises(ValueError, match="execution_policy_changed"):
            await repo.bootstrap(original)
        with pytest.raises(ExecutionCapacityUnavailable):
            await repo.prepare(scope, third.run_id, reduced)
    increased = ExecutionSlotPolicy(revision=3, global_limit=3)
    async with uow() as work:
        repo = DBEvaluationExecutionRepository(work.db_session)
        await repo.activate(increased, expected_revision=2)
        await repo.prepare(scope, third.run_id, increased)
        await work.commit()
    async with uow() as work:
        repo = DBEvaluationExecutionRepository(work.db_session)
        with pytest.raises(ValueError, match="execution_policy_changed"):
            await repo.activate(original, expected_revision=3)
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_execution_leases WHERE phase='prepared'")
            )
            == 3
        )


def run_command(fixture, binding, snapshot, kind, payload=None):
    from datetime import UTC, datetime

    from app.domain.execution.commands import CommandEnvelope

    if kind == "CreateRun":
        payload = {
            "family": snapshot.family.value,
            "source_entity_type": binding.source_entity_type,
            "source_entity_id": binding.source_entity_id,
            "semantic_payload": {},
            "public_input": {},
            "policy_snapshot": snapshot.model_dump(mode="json"),
        }
    return CommandEnvelope(
        command_id=uuid4(),
        command_type=kind,
        command_schema_version=1,
        stream_type="run",
        stream_id=str(binding.run_id),
        owner_user_id=fixture[1].user_id,
        team_id=fixture[1].team_id,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload=payload or {},
    )


async def test_actual_approval_resume_defers_atomically_until_reacquisition_and_terminal_releases(
    budget_binding_fixture,
):
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, uow = fixture
    first, snapshot = await bound_run(fixture)
    second, second_snapshot = await bound_run(fixture)
    policy = ExecutionSlotPolicy(revision=1, global_limit=1)
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(
        policy,
        session_factory=factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("execution-kernel"),
        evaluation_execution=guard,
    )
    async with uow() as work:
        await DBEvaluationExecutionRepository(work.db_session).prepare(scope, first.run_id, policy)
        await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(run_command(fixture, first, snapshot, kind))
        ).status == "accepted"
    approval = uuid4()
    assert (
        await handler.handle(
            run_command(
                fixture,
                first,
                snapshot,
                "RequestApproval",
                {
                    "approval_id": str(approval),
                    "subject_activity_id": str(uuid4()),
                    "approval_kind": "tool_call",
                    "risk_summary": "test",
                    "subject_label": "test",
                },
            )
        )
    ).status == "accepted"
    async with uow() as work:
        lease = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT phase,generation,state FROM evaluation_execution_leases WHERE run_id=:run"
                    ),
                    {"run": first.run_id},
                )
            )
            .mappings()
            .one()
        )
        assert lease["phase"] == "released"
        assert lease["state"]["status"] == "waiting"
        assert lease["generation"] == 2
        await DBEvaluationExecutionRepository(work.db_session).prepare(scope, second.run_id, policy)
        await work.commit()
    assert (
        await handler.handle(run_command(fixture, second, second_snapshot, "CreateRun"))
    ).status == "accepted"
    resume = run_command(
        fixture,
        first,
        snapshot,
        "DecideApproval",
        {"approval_id": str(approval), "decision": "approved", "actor_user_id": scope.user_id},
    )
    assert (await handler.handle(resume)).status == "deferred"
    async with uow() as work:
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE stream_id=:run AND event_type='ApprovalDecided'"
                ),
                {"run": str(first.run_id)},
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
            )
            == 1
        )
    assert (
        await handler.handle(
            run_command(fixture, second, second_snapshot, "CancelRun", {"reason": "test"})
        )
    ).status == "accepted"
    assert (await handler.handle(resume)).status == "accepted"
    async with uow() as work:
        lease = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT phase,generation,state FROM evaluation_execution_leases WHERE run_id=:run"
                    ),
                    {"run": first.run_id},
                )
            )
            .mappings()
            .one()
        )
        assert lease["phase"] == "held"
        assert lease["generation"] == 3
        assert lease["state"]["status"] == "running"
    assert (
        await handler.handle(run_command(fixture, first, snapshot, "CancelRun", {"reason": "test"}))
    ).status == "accepted"
    async with uow() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
            )
            == 0
        )


async def test_activity_gate_reads_accepted_state_and_current_namespace(budget_binding_fixture):
    from types import SimpleNamespace

    from app.application.execution.run_context import (
        RunContextUnavailableError,
        run_execution_context,
    )
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate, RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    policy = ExecutionSlotPolicy(revision=1)
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(
        policy,
        session_factory=factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=guard.authorization,
        evaluation_execution=guard,
    )
    async with uow() as work:
        await DBEvaluationExecutionRepository(work.db_session).prepare(
            scope, binding.run_id, policy
        )
        await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(run_command(fixture, binding, snapshot, kind))
        ).status == "accepted"
    from datetime import UTC, datetime, timedelta

    activity_id = uuid4()
    requested = run_command(
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
    assert (await handler.handle(requested)).status == "accepted"
    async with uow() as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": binding.run_id},
            )
        )
    context = run_execution_context(state)
    claim = SimpleNamespace(
        request=SimpleNamespace(
            activity_id=activity_id, generation=0, aggregate_id=str(binding.run_id)
        )
    )
    await guard.before_activity(claim, context)
    assert (
        await handler.handle(
            run_command(fixture, binding, snapshot, "WaitRun", {"reason": "approval"})
        )
    ).status == "accepted"
    with pytest.raises(RunContextUnavailableError):
        await guard.before_activity(claim, context)
    assert (
        await handler.handle(run_command(fixture, binding, snapshot, "ResumeRun"))
    ).status == "accepted"
    await guard.before_activity(claim, context)
    from tests.app.execution_test_support import execution_admin_session

    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": scope.user_id},
        )
        await db.commit()
    with pytest.raises(RunContextUnavailableError):
        await guard.before_activity(claim, context)
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version-1 WHERE id=:id"),
            {"id": scope.user_id},
        )
        await db.commit()
    async with uow() as work:
        await work.evaluation_budget_control.close(scope, binding.namespace_id, expected_revision=1)
        await work.commit()
    with pytest.raises(RunContextUnavailableError):
        await guard.before_activity(claim, context)


async def test_activity_gate_and_cancellation_use_two_sessions_without_lock_inversion(
    budget_binding_fixture, monkeypatch
):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.application.execution.activity_registry import ActivityRegistry
    from app.application.execution.activity_worker import ActivityWorker
    from app.application.execution.run_context import run_execution_context
    from app.application.execution.run_service import RunService
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate, RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.application.execution.test_activity_worker import Handler
    from tests.app.execution_test_support import authenticated_session_factory

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    engine = uow().session_factory.kw["bind"]
    other = create_async_engine(engine.url, pool_size=1, max_overflow=0)
    secret = load_deployment_settings().database_authorization_signing_secret
    factory = authenticated_session_factory(engine, signing_secret=secret)
    second = authenticated_session_factory(other, signing_secret=secret)
    authorization = AuthorizationContext.system("execution-kernel")
    policy = ExecutionSlotPolicy(revision=1)
    guard = EvaluationExecutionGuard(policy, session_factory=factory, authorization=authorization)
    second_guard = EvaluationExecutionGuard(
        policy, session_factory=second, authorization=authorization
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=authorization,
        evaluation_execution=guard,
    )
    canceller = SqlAlchemyExecutionOrchestrator(
        session_factory=second,
        aggregates={"run": RunAggregate()},
        authorization=authorization,
        evaluation_execution=second_guard,
    )
    tasks = []
    holding, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    try:
        async with uow() as work:
            await DBEvaluationExecutionRepository(work.db_session).prepare(
                scope, binding.run_id, policy
            )
            await work.commit()
        for kind in ("CreateRun", "StartRun"):
            assert (
                await handler.handle(run_command(fixture, binding, snapshot, kind))
            ).status == "accepted"
        activity_id = uuid4()
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
        async with uow() as work:
            state = RunState.model_validate(
                await work.db_session.scalar(
                    text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                    {"run": binding.run_id},
                )
            )

        class Contexts:
            async def load(self, run_id):
                return run_execution_context(state)

        original = DBEvaluationExecutionRepository.authorize

        async def pause_authorization(repo, *args):
            holding.set()
            await release.wait()
            return await original(repo, *args)

        monkeypatch.setattr(DBEvaluationExecutionRepository, "authorize", pause_authorization)

        class Gate:
            async def before_activity(self, claim, context):
                await guard.before_activity(claim, context)
                await cancelled.wait()

        activity = Handler(idempotent=True)
        registry = ActivityRegistry()
        registry.register(activity)
        # The fixture handler's declared type is the actual registered activity.
        assert activity.activity_type == "model.call"
        worker = ActivityWorker(
            store=PostgresActivityStore(session_factory=factory, authorization=authorization),
            run_contexts=Contexts(),
            run_service=RunService(orchestrator=handler),
            registry=registry,
            worker_id="c2-race",
            execution_gate=Gate(),
        )
        tasks.append(asyncio.create_task(worker.run_once(now=datetime.now(UTC), limit=1)))
        await asyncio.wait_for(holding.wait(), 3)

        async def cancel():
            try:
                return await canceller.handle(
                    run_command(fixture, binding, snapshot, "CancelRun", {"reason": "race"})
                )
            finally:
                cancelled.set()

        tasks.append(asyncio.create_task(cancel()))
        await asyncio.sleep(0.05)
        assert not tasks[1].done()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        assert not any(isinstance(item, BaseException) for item in results), results
        assert results[1].status == "accepted"
        assert results[0].stale == 1
        assert not activity.calls
        async with uow() as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
                )
                == 0
            )
    finally:
        release.set()
        cancelled.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await other.dispose()


async def test_recovery_keeps_unknown_prepared_and_rejects_outdated_generation_after_terminal(
    budget_binding_fixture,
):
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(
        ExecutionSlotPolicy(revision=1),
        session_factory=factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=guard.authorization,
        evaluation_execution=guard,
    )
    async with uow() as work:
        await DBEvaluationExecutionRepository(work.db_session).prepare(
            scope, binding.run_id, guard.policy
        )
        await work.commit()
    assert (await guard.reconcile(scope, binding.run_id, expected_generation=1))[
        "phase"
    ] == "prepared"
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(run_command(fixture, binding, snapshot, kind))
        ).status == "accepted"
    async with uow() as work:
        await work.evaluation_budget_control.close(scope, binding.namespace_id, expected_revision=1)
        await work.commit()
    cancel = run_command(fixture, binding, snapshot, "CancelRun", {"reason": "closed namespace"})
    assert (await handler.handle(cancel)).status == "accepted"
    assert (await handler.handle(cancel)).status == "accepted"
    with pytest.raises(ValueError, match="execution_lease_generation_stale"):
        await guard.reconcile(scope, binding.run_id, expected_generation=1)
    assert (await guard.reconcile(scope, binding.run_id, expected_generation=2))[
        "phase"
    ] == "released"
    assert (
        await handler.handle(run_command(fixture, binding, snapshot, "StartRun"))
    ).status == "rejected"
    async with uow() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
            )
            == 0
        )


async def test_operator_activation_and_startup_are_versioned_not_reinstallation(
    budget_binding_fixture, tmp_path
):
    from app.composition.evaluation_execution import (
        activate_execution_policy,
        build_evaluation_execution,
    )
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    _, _, _, _, _, _, uow = budget_binding_fixture
    settings = load_deployment_settings()
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=settings.database_authorization_signing_secret,
    )
    guard = await build_evaluation_execution(settings=settings, session_factory=factory)
    assert guard.policy == ExecutionSlotPolicy(revision=1)
    path = tmp_path / "execution-policy.json"
    path.write_text(ExecutionSlotPolicy(revision=2, subject_limit=3).model_dump_json())
    assert (
        await activate_execution_policy(session_factory=factory, path=path, expected_revision=1)
    ).revision == 2
    assert (
        await activate_execution_policy(session_factory=factory, path=path, expected_revision=1)
    ).revision == 2
    with pytest.raises(ValueError, match="execution_policy_changed"):
        await build_evaluation_execution(settings=settings, session_factory=factory)
    changed = settings.model_copy(
        update={"evaluation_execution_policy_revision": 2, "evaluation_subject_concurrency": 3}
    )
    assert (
        await build_evaluation_execution(settings=changed, session_factory=factory)
    ).policy.revision == 2


async def test_two_owners_and_workers_share_global_and_requester_execution_caps(
    budget_binding_fixture, configurations
):
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.domain.evaluation.configuration import SuiteDefinition, SuiteSettings
    from app.domain.evaluation.execution_slots import (
        ExecutionCapacityUnavailable,
        ExecutionSlotPolicy,
    )
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import authenticated_session_factory
    from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
        published_suite,
    )

    fixture = budget_binding_fixture
    service, scope, _principal, _, _, pair, uow = fixture
    owner, _ = await seed()
    other_scope, other_principal = OwnerScope.personal(owner), Principal(user_id=owner)
    old, config = await published_suite((service, configurations[1], other_scope, other_principal))
    definition = SuiteDefinition(
        dataset_version=old.dataset_version,
        config_versions=old.config_versions,
        rubric_version=old.rubric_version,
        mode="recorded",
        settings=SuiteSettings(token_budget=3000000),
    )
    draft = await service.create(
        other_scope,
        other_principal,
        kind="suite",
        name="Other owner",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    suite = await service.publish(
        other_scope,
        other_principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )

    def other_uow(authorization=None):
        return uow(
            authorization or AuthorizationContext.for_principal(other_principal, scope=other_scope)
        )

    second_fixture = (service, other_scope, other_principal, suite, config, pair, other_uow)
    first, _ = await bound_run(fixture, purpose="evaluation_judge")
    second, _ = await bound_run(second_fixture, purpose="evaluation_judge")
    original = ExecutionSlotPolicy(revision=1, global_limit=1, user_limit=1)
    async with uow() as work:
        await DBEvaluationExecutionRepository(work.db_session).bootstrap(original)
        await work.commit()
    engine = uow().session_factory.kw["bind"]
    other = create_async_engine(engine.url, pool_size=1, max_overflow=0)
    secret = load_deployment_settings().database_authorization_signing_secret
    factories = [
        authenticated_session_factory(engine, signing_secret=secret),
        authenticated_session_factory(other, signing_secret=secret),
    ]

    async def prepare(index, policy):
        async with factories[index]() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("execution-kernel")
            )
            result = await DBEvaluationExecutionRepository(session).prepare(
                [scope, other_scope][index], [first, second][index].run_id, policy
            )
            await session.commit()
            return result

    tasks = []
    try:
        tasks = [asyncio.create_task(prepare(i, original)) for i in range(2)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(item, ExecutionCapacityUnavailable) for item in results) == 1, results
        assert sum(isinstance(item, dict) for item in results) == 1, results
        winner = next(i for i, item in enumerate(results) if isinstance(item, dict))
        increased = ExecutionSlotPolicy(revision=2, global_limit=3, user_limit=1)
        async with uow() as work:
            await DBEvaluationExecutionRepository(work.db_session).activate(
                increased, expected_revision=1
            )
            await work.commit()
        await prepare(1 - winner, increased)
        extra, _ = await bound_run([fixture, second_fixture][winner], purpose="evaluation_judge")
        async with factories[winner]() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("execution-kernel")
            )
            with pytest.raises(ExecutionCapacityUnavailable):
                await DBEvaluationExecutionRepository(session).prepare(
                    [scope, other_scope][winner], extra.run_id, increased
                )
        async with uow() as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
                )
                == 2
            )
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await other.dispose()


async def test_revoked_requester_defers_new_work_but_terminal_releases(budget_binding_fixture):
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_admin_session,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(
        ExecutionSlotPolicy(revision=1),
        session_factory=factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=guard.authorization,
        evaluation_execution=guard,
    )
    async with uow() as work:
        await work.evaluation_execution.prepare(scope, binding.run_id, guard.policy)
        await work.commit()
    for kind in ("CreateRun", "StartRun", "WaitRun"):
        assert (
            await handler.handle(
                run_command(
                    fixture,
                    binding,
                    snapshot,
                    kind,
                    {"reason": "test"} if kind == "WaitRun" else None,
                )
            )
        ).status == "accepted"
    held, held_snapshot = await bound_run(fixture)
    async with uow() as work:
        await work.evaluation_execution.prepare(scope, held.run_id, guard.policy)
        await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (
            await handler.handle(run_command(fixture, held, held_snapshot, kind))
        ).status == "accepted"
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    assert (
        await handler.handle(run_command(fixture, binding, snapshot, "ResumeRun"))
    ).status == "deferred"
    assert (
        await handler.handle(
            run_command(fixture, binding, snapshot, "CancelRun", {"reason": "revoked"})
        )
    ).status == "accepted"
    assert (
        await handler.handle(
            run_command(fixture, held, held_snapshot, "CancelRun", {"reason": "revoked while held"})
        )
    ).status == "accepted"
    async with uow(guard.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key='0:global'")
            )
            == 0
        )


async def test_ordinary_runs_bypass_evaluation_pool_locks_and_old_unbound_evaluations_fail_closed(
    budget_binding_fixture, monkeypatch
):
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    fixture = budget_binding_fixture
    _, _, _, _, _, _, uow = fixture
    binding, snapshot = await bound_run(fixture)
    factory = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    guard = EvaluationExecutionGuard(
        ExecutionSlotPolicy(revision=1),
        session_factory=factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=guard.authorization,
        evaluation_execution=guard,
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("ordinary run acquired evaluation pool locks")

    monkeypatch.setattr(DBEvaluationExecutionRepository, "lock", forbidden)
    ordinary = binding.model_copy(update={"run_id": uuid4(), "source_entity_type": "session"})
    for kind in ("CreateRun", "StartRun", "CancelRun"):
        assert (
            await handler.handle(
                run_command(
                    fixture,
                    ordinary,
                    snapshot,
                    kind,
                    {"reason": "test"} if kind == "CancelRun" else None,
                )
            )
        ).status == "accepted"
    missing = binding.model_copy(update={"run_id": uuid4()})
    assert (
        await handler.handle(run_command(fixture, missing, snapshot, "CreateRun"))
    ).status == "deferred"
    # Simulate an accepted historical evaluation before budget binding existed.
    historical = SqlAlchemyExecutionOrchestrator(
        session_factory=factory,
        aggregates={"run": RunAggregate()},
        authorization=guard.authorization,
    )
    assert (
        await historical.handle(run_command(fixture, missing, snapshot, "CreateRun"))
    ).status == "accepted"
    assert (
        await handler.handle(run_command(fixture, missing, snapshot, "StartRun"))
    ).status == "deferred"


async def test_execution_tables_deny_api_and_kernel_identity_mutation(
    budget_binding_fixture, configurations
):
    from sqlalchemy.exc import DBAPIError

    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.models.authorization import AuthorizationContext

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, uow = fixture
    binding, _ = await bound_run(fixture)
    async with uow() as work:
        assert await work.db_session.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        await work.evaluation_execution.prepare(
            scope, binding.run_id, ExecutionSlotPolicy(revision=1)
        )
        await work.commit()
    for statement in (
        "UPDATE evaluation_execution_leases SET namespace_id=namespace_id",
        "UPDATE evaluation_execution_policy_versions SET body=body",
        "DELETE FROM evaluation_execution_leases",
    ):
        async with uow() as work:
            with pytest.raises(DBAPIError, match="permission denied"):
                await work.db_session.execute(text(statement))
    api_uow = configurations[0].uow_factory
    for statement in (
        "SELECT * FROM evaluation_execution_leases",
        "INSERT INTO evaluation_execution_pools(key,occupied) VALUES('forged',0)",
        "UPDATE evaluation_execution_policy_head SET revision=1",
    ):
        async with api_uow(AuthorizationContext.for_principal(principal, scope=scope)) as work:
            with pytest.raises(DBAPIError, match="permission denied"):
                await work.db_session.execute(text(statement))
