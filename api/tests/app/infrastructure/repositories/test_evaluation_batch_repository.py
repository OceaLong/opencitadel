# ruff: noqa: F401,F811
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

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_intent_duplicate_conflict_and_fenced_materialization(budget_binding_fixture):
    from app.domain.evaluation.batch import schedule_slots
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    _service, scope, principal, suite, _config, _pair, factory = budget_binding_fixture
    batch_id = uuid4()
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        repo = DBEvaluationBatchRepository(work.db_session)
        first = await repo.submit(
            scope, principal, "start", "request", {"suite_version": str(suite.id)}, batch_id
        )
        assert (
            await repo.submit(
                scope, principal, "start", "request", {"suite_version": str(suite.id)}, uuid4()
            )
            == first
        )
        with pytest.raises(ValueError, match="request_conflict"):
            await repo.submit(
                scope, principal, "start", "request", {"suite_version": str(uuid4())}, uuid4()
            )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_results")) == 0
        )
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = DBEvaluationBatchRepository(work.db_session)
        claim = await repo.claim(datetime.now(UTC), lease_seconds=1)
        assert claim["id"] == batch_id
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = DBEvaluationBatchRepository(work.db_session)
        replacement = await repo.claim(datetime.now(UTC) + timedelta(seconds=2), lease_seconds=60)
        assert replacement["generation"] == claim["generation"] + 1
        with pytest.raises(ValueError, match="claim_lost"):
            await repo.fence(claim)
        slots = schedule_slots([uuid4() for _ in range(1000)], [uuid4() for _ in range(5)], 1, 42)
        await repo.materialize(replacement, slots, suite.settings.model_dump(mode="json"))
        await repo.materialize(replacement, slots, suite.settings.model_dump(mode="json"))
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_results"))
            == 5000
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_attempts"))
            == 5000
        )
        first_pass = await repo.dispatch_candidates(replacement, datetime.now(UTC), limit=3)
        assert [row["ordinal"] for row in first_pass] == [0, 1, 2]
        # Waiting candidates retain eligibility, but the next bounded pass advances.
        for row in first_pass:
            await repo.update_result(scope, batch_id, row, "waiting")
        second_pass = await repo.dispatch_candidates(replacement, datetime.now(UTC), limit=3)
        assert [row["ordinal"] for row in second_pass] == [3, 4, 5]
        await work.commit()


async def test_service_start_is_durable_intent_and_cancel_stops_claim(budget_binding_fixture):
    from app.application.evaluation.batch_service import BatchService
    from app.application.evaluation.preflight import PreflightService
    from app.application.evaluation.recording_authority import RecordingPreflightAuthority
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    suites, scope, principal, suite, _config, _pair, factory = budget_binding_fixture

    def preflights(principal):
        return PreflightService(suites, principal, recordings=RecordingPreflightAuthority())

    check = await preflights(principal).check(scope, suite.id)
    service = BatchService(suites, preflight_factory=preflights)
    payload = {"suite_version": str(suite.id), "preflight_revision": check.revision}
    first = await service.start(scope, principal, "start-request", payload)
    duplicate = await service.start(scope, principal, "start-request", payload)
    assert first.id == duplicate.id
    assert first.status == "created"
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_results")) == 0
        )
        repo = DBEvaluationBatchRepository(work.db_session)
        claim = await repo.claim(datetime.now(UTC))
        await work.commit()
    await service.cancel(scope, principal, "cancel-request", {"batch_id": str(first.id)})
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(ValueError, match="batch_dispatch_stopped"):
            await DBEvaluationBatchRepository(work.db_session).fence(claim, dispatch=True)


async def scheduled_batch(budget_binding_fixture, *, preflights=None):
    from types import SimpleNamespace

    from app.application.evaluation.batch_service import BatchService
    from app.application.evaluation.preflight import PreflightService
    from app.application.evaluation.recording_authority import RecordingPreflightAuthority
    from app.application.evaluation.scheduler import Scheduler
    from app.application.execution.admission import RunAdmissionService
    from app.application.execution.command_ingress import CommandIngress
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    suites, scope, principal, suite, _config, pair, factory = budget_binding_fixture

    class Heads:
        async def active_execution(self, **kwargs):
            return pair.execution

    class Objects:
        async def put_input(self, run, body):
            from app.domain.evaluation.configuration import digest

            return f"test/{run}/input", digest(body)

    class Writer:
        async def receive(self, *args, **kwargs):
            raise AssertionError("scheduler must supply atomic sink")

    admission = RunAdmissionService(
        command_ingress=CommandIngress(writer=Writer()),
        activity_objects=Objects(),
        policy_heads=Heads(),
    )
    preflights = preflights or (
        lambda principal: PreflightService(
            suites, principal, recordings=RecordingPreflightAuthority()
        )
    )
    check = await preflights(principal).check(scope, suite.id)
    service = BatchService(suites, preflight_factory=preflights)
    batch = await service.start(
        scope,
        principal,
        "start-scheduler",
        {"suite_version": str(suite.id), "preflight_revision": check.revision},
    )
    scheduler = Scheduler(
        factory,
        suites,
        admission,
        execution_policy=ExecutionSlotPolicy(revision=1),
        preflight_factory=preflights,
    )
    return service, scheduler, batch


async def test_scheduler_enqueue_is_not_acceptance_and_early_cancel_waits(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    _suites, scope, principal, _suite, _config, _pair, factory = budget_binding_fixture
    service, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    tick = await scheduler.tick(datetime.now(UTC))
    assert tick.claimed == 1
    assert tick.dispatched == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = DBEvaluationBatchRepository(work.db_session)
        row = (await repo.results(scope, batch.id))[0]
        assert row["admission_status"] == "submitted"
        assert row["execution_status"] == "admitting"
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_run_projection")) == 0
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_replay_bindings"))
            == 1
        )
        row["command_id"]
    await service.cancel(scope, principal, "cancel-scheduler", {"batch_id": str(batch.id)})
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='CancelRun'")
            )
            == 0
        )
        assert (await DBEvaluationBatchRepository(work.db_session).get(scope, batch.id))[
            "status"
        ] == "cancelled"
        assert (await work.evaluation_budget_control.namespace(scope, batch.id)).state == "closed"


async def test_never_accepted_withdrawal_releases_only_prepared_execution(budget_binding_fixture):
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_inbox import PostgresInbox
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_execution_slots import (
        bound_run,
        run_command,
    )

    binding, snapshot = await bound_run(budget_binding_fixture)
    _, scope, _, _, _, _, factory = budget_binding_fixture
    command = run_command(budget_binding_fixture, binding, snapshot, "CreateRun")
    policy = ExecutionSlotPolicy(revision=1)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_execution.prepare(scope, binding.run_id, policy)
        await PostgresInbox(work.db_session).receive(command)
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_execution.withdraw_unaccepted(
            scope, binding.run_id, command.command_id, policy
        )
        assert await work.evaluation_execution.withdraw_unaccepted(
            scope, binding.run_id, command.command_id, policy
        )
        assert (
            await work.db_session.scalar(
                text("SELECT sum(occupied) FROM evaluation_execution_pools")
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT status FROM execution_command_inbox WHERE command_id=:id"),
                {"id": command.command_id},
            )
            == "rejected"
        )
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        # Same original command cannot get a fresh accepted attempt after withdrawal.
        assert not await PostgresInbox(work.db_session).receive(command)
        assert (
            await work.db_session.scalar(
                text("SELECT phase FROM evaluation_execution_leases WHERE run_id=:id"),
                {"id": binding.run_id},
            )
            == "released"
        )


async def test_batch_http_202_and_public_details_do_not_expose_intents(budget_binding_fixture):
    import httpx
    from fastapi import FastAPI

    from app.application.evaluation.batch_service import BatchService
    from app.application.evaluation.preflight import PreflightService
    from app.application.evaluation.recording_authority import RecordingPreflightAuthority
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_batch_routes import router
    from app.interfaces.service_dependencies import get_batch_service

    suites, scope, principal, suite, _config, _pair, _factory = budget_binding_fixture

    def preflights(principal):
        return PreflightService(suites, principal, recordings=RecordingPreflightAuthority())

    check = await preflights(principal).check(scope, suite.id)
    service = BatchService(suites, preflight_factory=preflights)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_batch_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/evaluation/batches",
            json={
                "suite_version": str(suite.id),
                "preflight_revision": check.revision,
                "request_id": "http-start",
            },
        )
        assert response.status_code == 202, response.text
        identity = response.json()["data"]["id"]
        detail = await client.get(f"/evaluation/batches/{identity}")
        assert detail.status_code == 200
        assert "private_input" not in detail.text
        assert "principal" not in detail.text
        assert "envelope" not in detail.text
        response = await client.post(
            f"/evaluation/batches/{identity}/commands/cancel", json={"request_id": "http-cancel"}
        )
        assert response.status_code == 202


def actual_handler(fixture, policy):
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    sessions = authenticated_session_factory(
        fixture[-1]().session_factory.kw["bind"],
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )
    return SqlAlchemyExecutionOrchestrator(
        session_factory=sessions,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("execution-kernel"),
        evaluation_execution=EvaluationExecutionGuard(
            policy,
            session_factory=sessions,
            authorization=AuthorizationContext.system("execution-kernel"),
        ),
    )


async def test_accepted_cancel_and_late_rejection_never_revive_batch(budget_binding_fixture):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    handler = actual_handler(fixture, scheduler.execution_policy)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
    assert (
        await handler.handle(CommandEnvelope.model_validate(row["envelope"]))
    ).status == "accepted"
    await service.cancel(scope, principal, "cancel-accepted", {"batch_id": str(batch.id)})
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
        command = (
            (
                await work.db_session.execute(
                    text("SELECT * FROM execution_command_inbox WHERE command_id=:id"),
                    {"id": row["cancel_command_id"]},
                )
            )
            .mappings()
            .one()
        )
        fields = {k: command[k] for k in CommandEnvelope.model_fields if k in command}
    assert (await handler.handle(CommandEnvelope.model_validate(fields))).status == "accepted"
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    assert (await service.get(scope, principal, batch.id)).status == "cancelled"
    late = CommandEnvelope.model_validate(row["envelope"]).model_copy(
        update={
            "command_id": uuid4(),
            "command_type": "CompleteRun",
            "expected_stream_version": None,
            "payload": {},
        }
    )
    assert (await handler.handle(late)).status == "rejected"
    await scheduler.tick(datetime.now(UTC))
    assert (await service.get(scope, principal, batch.id)).status == "cancelled"


async def test_unknown_effect_is_recorded_and_not_eligible_for_manual_retry(budget_binding_fixture):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    handler = actual_handler(fixture, scheduler.execution_policy)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"
    assert (
        await handler.handle(
            create.model_copy(
                update={
                    "command_id": uuid4(),
                    "command_type": "StartRun",
                    "expected_stream_version": None,
                    "payload": {},
                }
            )
        )
    ).status == "accepted"
    failure = create.model_copy(
        update={
            "command_id": uuid4(),
            "command_type": "FailRun",
            "expected_stream_version": None,
            "payload": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
        }
    )
    assert (await handler.handle(failure)).status == "accepted"
    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
        assert row["unknown_effect"] is True
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_attempts"))
            == 1
        )
    with pytest.raises(ValueError, match="no_retryable_results"):
        await service.retry_failed(
            scope, principal, "no-unknown-retry", {"batch_id": str(batch.id)}
        )


async def test_two_session_acceptance_races_with_durable_withdrawal(budget_binding_fixture):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
    second_engine = create_async_engine(
        factory().session_factory.kw["bind"].url, pool_size=1, max_overflow=0
    )

    def second_factory(authorization=None):
        work = factory(authorization)
        work.session_factory = async_sessionmaker(second_engine, expire_on_commit=False)
        return work

    try:
        handler = actual_handler((*fixture[:-1], second_factory), scheduler.execution_policy)
        await service.cancel(scope, principal, "race-cancel", {"batch_id": str(batch.id)})
        outcomes = await asyncio.gather(
            handler.handle(CommandEnvelope.model_validate(row["envelope"])),
            scheduler.tick(datetime.now(UTC)),
            return_exceptions=True,
        )
        assert not any(isinstance(value, BaseException) for value in outcomes), outcomes
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            receipt = await DBEvaluationBatchRepository(work.db_session).receipt(scope, row)
            lease = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT phase,accepted_version FROM evaluation_execution_leases WHERE run_id=:id"
                        ),
                        {"id": row["run_id"]},
                    )
                )
                .mappings()
                .one()
            )
            if receipt["status"] == "rejected":
                assert lease["phase"] == "released"
                assert lease["accepted_version"] == 0
                assert (
                    await work.db_session.scalar(text("SELECT count(*) FROM execution_events")) == 0
                )
            else:
                assert receipt["status"] == "accepted"
                assert lease["accepted_version"] > 0
                assert (
                    await work.db_session.scalar(
                        text(
                            "SELECT count(*) FROM execution_command_inbox WHERE command_type='CancelRun'"
                        )
                    )
                    == 1
                )
        replay = await handler.handle(CommandEnvelope.model_validate(row["envelope"]))
        assert replay.status == receipt["status"]
    finally:
        await second_engine.dispose()


async def isolated_scheduled_batch(budget_binding_fixture):
    from app.application.evaluation.environment_authority import EnvironmentPreflightAuthority
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.evaluation.preflight import PreflightService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.configuration import SuiteDefinition, SuiteSettings
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    fixture = budget_binding_fixture
    suites, scope, principal, recorded, _config, _pair, factory = fixture
    value = version()

    class Adapter:
        revision = "1"
        fixture_revisions = frozenset({"1"})
        healthcheck_revisions = frozenset({"1"})
        tool_names = frozenset()

        def validate(self, value, targets):
            assert targets == ()

    registry = AdapterRegistry(adapters={"test": Adapter()})
    environments = EnvironmentService(factory, registry)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_environment.register(scope, "environment", value)
        await work.commit()
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="isolated scheduler",
        definition=SuiteDefinition(
            dataset_version=recorded.dataset_version,
            config_versions=recorded.config_versions,
            rubric_version=recorded.rubric_version,
            mode="isolated",
            environment_version=value.id,
            settings=SuiteSettings(token_budget=3000000),
        ).model_dump(mode="json"),
        request_id="isolated-suite",
    )
    suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id="isolated-publish",
    )

    def preflights(principal):
        return PreflightService(
            suites, principal, environments=EnvironmentPreflightAuthority(registry)
        )

    service, scheduler, batch = await scheduled_batch(
        (*fixture[:3], suite, *fixture[4:]), preflights=preflights
    )
    scheduler.environments = environments
    return service, scheduler, batch


async def test_isolated_scheduler_waits_for_ready_then_binds_real_environment(
    budget_binding_fixture,
):
    from app.domain.evaluation.environment import transition
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    _, scope, principal, _, _, _, factory = budget_binding_fixture
    service, scheduler, batch = await isolated_scheduled_batch(budget_binding_fixture)
    first = await scheduler.tick(datetime.now(UTC))
    assert first.dispatched == 0
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        leases = (
            (await work.db_session.execute(text("SELECT id FROM evaluation_environment_leases")))
            .scalars()
            .all()
        )
        assert len(leases) == 1
        lease = await work.evaluation_environment.lease(scope, leases[0])
        assert lease.requester["user_id"] == principal.user_id
        assert lease.state == "preparing"
        await work.evaluation_environment.save(scope, lease, transition(lease, "ready"))
        await work.commit()
    second = await scheduler.tick(datetime.now(UTC))
    assert second.dispatched == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
        assert row["envelope"]["payload"]["source_entity_type"] == "evaluation_isolated_case"
        assert (await work.evaluation_environment.lease(scope, leases[0])).state == "leased"
    await service.cancel(scope, principal, "isolated-cancel", {"batch_id": str(batch.id)})
    await scheduler.tick(datetime.now(UTC))
    view = await service.get(scope, principal, batch.id)
    assert view.status == "cancelled"
    assert view.cleanup_status == "pending"
    for phase, evidence in (("cleanup", {}), ("verify_clean", {"verified": True, "resources": []})):
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            identity = await work.db_session.scalar(
                text(
                    "SELECT id FROM evaluation_environment_operations WHERE phase=:phase AND status='queued'"
                ),
                {"phase": phase},
            )
            operation, _ = await work.evaluation_environment.claim(scope, identity)
            await work.commit()
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            assert await work.evaluation_environment.complete(scope, operation, evidence)
            await work.commit()
    await scheduler.tick(datetime.now(UTC))
    view = await service.get(scope, principal, batch.id)
    assert view.status == "cancelled"
    assert view.cleanup_status == "clean"


async def test_redelivery_uses_existing_admission_after_policy_reader_disappears(
    budget_binding_fixture,
):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, _principal, _, _, _, factory = fixture
    _service, scheduler, batch = await scheduled_batch(fixture)
    original_admit = scheduler.admission.admit

    async def lost_acknowledgement(**kwargs):
        await original_admit(**kwargs)
        raise ConnectionError("process lost acknowledgement after commit")

    scheduler.admission.admit = lost_acknowledgement
    with pytest.raises(ConnectionError, match="lost acknowledgement"):
        await scheduler.tick(datetime.now(UTC))
    scheduler.admission.admit = original_admit

    class UnavailableHeads:
        async def active_execution(self, **kwargs):
            raise AssertionError("existing submitted command must not be re-admitted")

    scheduler.admission._policy_heads = UnavailableHeads()
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
        assert row["execution_status"] == "admitting"
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='CreateRun'")
            )
            == 1
        )


async def test_batch_timeout_withdraws_pending_intent_and_finishes_failed(budget_binding_fixture):
    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, _ = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    await scheduler.tick(datetime.now(UTC) + timedelta(days=2))
    result = await service.get(scope, principal, batch.id)
    assert result.status == "failed"


@pytest.mark.parametrize("change", ["revoked", "policy"])
async def test_pending_cancel_resolves_actual_deferred_create(budget_binding_fixture, change):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )
    from tests.app.execution_test_support import execution_admin_session

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
    await service.cancel(scope, principal, "deferred-cancel", {"batch_id": str(batch.id)})
    if change == "revoked":
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
    else:
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            await work.evaluation_execution.activate(
                scheduler.execution_policy.model_copy(update={"revision": 2}), expected_revision=1
            )
            await work.commit()
    handler = actual_handler(fixture, scheduler.execution_policy)
    assert (
        await handler.handle(CommandEnvelope.model_validate(row["envelope"]))
    ).status == "deferred"
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = DBEvaluationBatchRepository(work.db_session)
        assert (await repo.get(scope, batch.id))["status"] == "cancelled"
        assert (await repo.receipt(scope, row))["status"] == "rejected"
        assert (
            await work.db_session.scalar(
                text("SELECT sum(occupied) FROM evaluation_execution_pools")
            )
            == 0
        )


@pytest.mark.parametrize(
    ("mode", "resolution"),
    [
        ("recorded", "clean"),
        ("isolated", "clean"),
        ("isolated", "case_timeout"),
        ("isolated", "cancel"),
        ("isolated", "revoked"),
        ("isolated", "quarantine"),
    ],
)
async def test_safe_pre_call_timeout_creates_linked_bounded_replacement(
    budget_binding_fixture, mode, resolution
):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, _principal, _, _, _, factory = fixture
    make_batch = isolated_scheduled_batch if mode == "isolated" else scheduled_batch
    _service, scheduler, batch = await make_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    if mode == "isolated":
        await ready_pending_environment(fixture)
        assert (await scheduler.tick(datetime.now(UTC))).dispatched == 1
    handler = actual_handler(fixture, scheduler.execution_policy)
    originals = []
    for attempt in range(3):
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
        originals.append(row["run_id"])
        assert row["attempt"] == attempt
        create = CommandEnvelope.model_validate(row["envelope"])
        assert (await handler.handle(create)).status == "accepted"

        async def command(kind, payload, version=1, *, create=create):
            return await handler.handle(
                create.model_copy(
                    update={
                        "command_id": uuid4(),
                        "command_type": kind,
                        "command_schema_version": version,
                        "expected_stream_version": None,
                        "payload": payload,
                    }
                )
            )

        assert (await command("StartRun", {})).status == "accepted"
        activity = uuid4()
        assert (
            await command(
                "RequestActivity",
                {
                    "activity_id": str(activity),
                    "activity_type": "model.call",
                    "timeout_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                    "input_ref": "test/input",
                    "input_digest": "a" * 64,
                },
                2,
            )
        ).status == "accepted"
        assert (
            await command(
                "FailActivity",
                {"activity_id": str(activity), "generation": 0, "failure_code": "ACTIVITY_TIMEOUT"},
            )
        ).status == "accepted"
        assert (
            await command("FailRun", {"failure_code": "ACTIVITY_TIMEOUT", "retryable": False})
        ).status == "accepted"
        await PostgresFormalProjector(
            session_factory=handler._session_factory,
            authorization=AuthorizationContext.system("execution-kernel"),
        ).run_once(scope, limit=100)
        checkpoint = datetime.now(UTC) + timedelta(seconds=10 * (attempt + 1))
        assert (await scheduler.tick(checkpoint)).dispatched == 0
        if mode == "isolated":
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                saved = await work.evaluation_batch.get(scope, batch.id)
                result = (await work.evaluation_batch.results(scope, batch.id))[0]
                if attempt < 2:
                    assert saved["status"] == "waiting"
                    assert result["recovery_pending"]
                    assert result["execution_status"] == "failed"
                    assert (
                        await work.evaluation_budget_control.namespace(scope, batch.id)
                    ).state == "open"
            if attempt < 2:
                with pytest.raises(ValueError, match="no_retryable_results"):
                    await _service.retry_failed(
                        scope,
                        fixture[2],
                        "pending-recovery-" + str(attempt),
                        {"batch_id": str(batch.id)},
                    )
            if attempt == 0 and resolution != "clean":
                _, _, principal, _, _, _, _ = fixture
                next_tick = checkpoint + timedelta(seconds=1)
                if resolution == "case_timeout":
                    next_tick += timedelta(minutes=31)
                elif resolution == "cancel":
                    await _service.cancel(
                        scope, principal, "pending-recovery-cancel", {"batch_id": str(batch.id)}
                    )
                elif resolution == "revoked":
                    from tests.app.execution_test_support import execution_admin_session

                    async with execution_admin_session() as db:
                        await db.execute(
                            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                            {"id": principal.user_id},
                        )
                        await db.commit()
                else:
                    from app.domain.evaluation.environment import transition

                    async with factory(AuthorizationContext.system("execution-kernel")) as work:
                        lease_id = await work.db_session.scalar(
                            text(
                                "SELECT id FROM evaluation_environment_leases WHERE state='cleaning'"
                            )
                        )
                        lease = await work.evaluation_environment.lease(scope, lease_id)
                        await work.evaluation_environment.save(
                            scope, lease, transition(lease, "quarantine"), error="cleanup_failed"
                        )
                        await work.commit()
                await scheduler.tick(next_tick)
                async with factory(AuthorizationContext.system("execution-kernel")) as work:
                    result = (await work.evaluation_batch.results(scope, batch.id))[0]
                    assert not result["recovery_pending"]
                    assert result["attempt"] == 0
                    assert (
                        result["error"]
                        == {
                            "case_timeout": "case_timeout",
                            "cancel": "batch_stopped",
                            "revoked": "retry_authorization_revoked",
                            "quarantine": "cleanup_failed",
                        }[resolution]
                    )
                    assert (await work.evaluation_batch.get(scope, batch.id))["status"] == (
                        "cancelled" if resolution == "cancel" else "completed_with_errors"
                    )
                    assert (
                        await work.evaluation_budget_control.namespace(scope, batch.id)
                    ).state == "closed"
                return
            old_operations = await complete_pending_environment_cleanup(fixture)
            await scheduler.tick(checkpoint + timedelta(seconds=1))
        if attempt < 2:
            if mode == "isolated":
                assert (await scheduler.tick(checkpoint + timedelta(seconds=5))).dispatched == 0
                async with factory(AuthorizationContext.system("execution-kernel")) as work:
                    for operation, evidence in old_operations:
                        await work.evaluation_environment.complete(scope, operation, evidence)
                    leases = (
                        await work.db_session.execute(
                            text(
                                "SELECT generation,state FROM evaluation_environment_leases ORDER BY generation"
                            )
                        )
                    ).all()
                    assert leases == [
                        (value + 1, "verified_clean") for value in range(attempt + 1)
                    ] + [(attempt + 2, "preparing")]
                    await work.commit()
                # Re-delivery must use the same lease/generation, not a new allocation.
                assert (await scheduler.tick(checkpoint + timedelta(seconds=5))).dispatched == 0
                await ready_pending_environment(fixture)
            assert (await scheduler.tick(checkpoint + timedelta(seconds=6))).dispatched == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_attempts"))
            == 3
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM evaluation_run_lineages WHERE predecessor_run_id IS NOT NULL"
                )
            )
            == 2
        )
        assert len(set(originals)) == 3


@pytest.mark.parametrize("boundary", ["timer", "deadletter"])
@pytest.mark.parametrize(
    ("kind", "idempotent", "started", "redirected"),
    [
        ("model.call", False, True, True),
        ("model.call", False, False, False),
        ("model.call", True, True, False),
        ("tool.call", False, True, False),
    ],
)
async def test_timer_after_real_call_start_redirects_to_durable_unknown(
    budget_binding_fixture, kind, idempotent, started, redirected, boundary
):
    from app.application.execution.activity_registry import ActivityRegistry
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_activity_timeout import PostgresActivityTimeoutGuard
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, factory = fixture
    _, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    registry = ActivityRegistry()

    class Handler:
        activity_type = kind

    Handler.idempotent = idempotent
    registry.register(Handler())
    handler = actual_handler(fixture, scheduler.execution_policy)
    handler._activity_timeout = PostgresActivityTimeoutGuard(registry)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await DBEvaluationBatchRepository(work.db_session).results(scope, batch.id))[0]
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1):
        return await handler.handle(
            create.model_copy(
                update={
                    "command_id": uuid4(),
                    "command_type": kind,
                    "command_schema_version": version,
                    "expected_stream_version": None,
                    "payload": payload,
                }
            )
        )

    assert (await command("StartRun", {})).status == "accepted"
    activity = uuid4()
    assert (
        await command(
            "RequestActivity",
            {
                "activity_id": str(activity),
                "activity_type": kind,
                "timeout_at": datetime.now(UTC).isoformat(),
                "input_ref": "test/input",
                "input_digest": "a" * 64,
            },
            2,
        )
    ).status == "accepted"
    if started:
        assert (
            await command(
                "MarkActivityCallStarted", {"activity_id": str(activity), "generation": 0}, 2
            )
        ).status == "accepted"
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        timer = (
            await work.db_session.execute(
                text(
                    "SELECT command_envelope FROM execution_scheduled_commands WHERE cancellation_activity_id=:id"
                ),
                {"id": activity},
            )
        ).scalar_one()
    timeout = CommandEnvelope.model_validate(timer)
    if boundary == "deadletter":
        from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore

        store = PostgresActivityStore(
            session_factory=handler._session_factory,
            authorization=AuthorizationContext.system("execution-kernel"),
            max_claim_attempts=1,
        )
        assert (
            len(
                await store.claim(
                    now=datetime.now(UTC),
                    limit=1,
                    worker_id="first-claim",
                    claim_ttl=timedelta(seconds=1),
                )
            )
            == 1
        )
        assert not await store.claim(
            now=datetime.now(UTC) + timedelta(seconds=2),
            limit=1,
            worker_id="cap-claim",
            claim_ttl=timedelta(seconds=1),
        )
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            row = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT * FROM execution_command_inbox WHERE command_type='FailActivity'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            timeout = CommandEnvelope.model_validate(
                {k: row[k] for k in CommandEnvelope.model_fields if k in row}
            )
    assert (await handler.handle(timeout)).status == ("rejected" if redirected else "accepted")
    if not redirected:
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_events WHERE event_type='ActivityFailed'")
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM execution_command_inbox WHERE command_type='MarkActivityOutcomeUnknown'"
                    )
                )
                == 0
            )
        return
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        unknown = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT * FROM execution_command_inbox WHERE command_type='MarkActivityOutcomeUnknown'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_events WHERE event_type='ActivityFailed'")
            )
            == 0
        )
        saved = CommandEnvelope.model_validate(
            {k: unknown[k] for k in CommandEnvelope.model_fields if k in unknown}
        )
    assert (await handler.handle(saved)).status == "accepted"
    assert (await handler.handle(timeout)).status == "rejected"


async def test_scheduler_f07_snapshot_keeps_original_requester_not_kernel(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    fixture = budget_binding_fixture
    _, _scope, principal, _, _, _, factory = fixture
    _, scheduler, _ = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        bodies = (
            (await work.db_session.execute(text("SELECT body FROM execution_configurations")))
            .scalars()
            .all()
        )
        assert len(bodies) == 1
        assert bodies[0].get("stage") == "admission"
        proof = bodies[0]["physical_requester"]["proof"]
        assert proof["kind"] == "user"
        assert proof["principal"]["user_id"] == principal.user_id


async def test_spent_batch_budget_blocks_undispatched_result_without_run(budget_binding_fixture):
    from app.domain.evaluation.budget import BudgetBucket, BudgetSettlement
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import (
        demand,
        repository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    value = demand(scope, principal, batch=str(batch.id), tokens=3000000).model_copy(
        update={
            "policy_revision": 1,
            "buckets": (
                BudgetBucket(key="0:global", slots=10),
                BudgetBucket(key="5:batch:" + str(batch.id), tokens=3000000),
            ),
        }
    )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = repository(work)
        call = str(uuid4())
        await repo.reserve(call, value)
        await repo.settle(
            call, value, BudgetSettlement(tokens=3000000, evidence="accepted-original-usage")
        )
        await work.commit()
    await scheduler.tick(datetime.now(UTC))
    result = await service.get(scope, principal, batch.id)
    assert result.status == "completed_with_errors"
    assert result.counts == {"blocked_budget": 1}
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='CreateRun'")
            )
            == 0
        )


async def test_reserved_budget_waits_then_low_usage_settlement_admits(budget_binding_fixture):
    from app.domain.evaluation.budget import BudgetBucket, BudgetSettlement
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import (
        demand,
        repository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    value = demand(scope, principal, batch=str(batch.id), tokens=3000000).model_copy(
        update={
            "policy_revision": 1,
            "buckets": (
                BudgetBucket(key="0:global", slots=10),
                BudgetBucket(key="5:batch:" + str(batch.id), tokens=3000000),
            ),
        }
    )
    call = str(uuid4())
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        await repository(work).reserve(call, value)
        await work.commit()
    await scheduler.tick(datetime.now(UTC))
    assert (await service.get(scope, principal, batch.id)).status == "waiting"
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='CreateRun'")
            )
            == 0
        )
        await repository(work).settle(
            call, value, BudgetSettlement(tokens=0, evidence="accepted-known-zero-usage")
        )
        await work.commit()
    assert (await scheduler.tick(datetime.now(UTC))).dispatched == 1
    assert (await service.get(scope, principal, batch.id)).counts == {"admitting": 1}


async def test_case_wallclock_timeout_withdraws_pending_create(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    await scheduler.tick(datetime.now(UTC) + timedelta(minutes=31))
    result = await service.get(scope, principal, batch.id)
    assert result.status == "completed_with_errors"
    assert result.counts == {"failed": 1}
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT error FROM evaluation_batch_results"))
            == "case_timeout"
        )
        assert (
            await work.db_session.scalar(
                text("SELECT status FROM execution_command_inbox WHERE command_type='CreateRun'")
            )
            == "rejected"
        )
        assert (
            await work.db_session.scalar(
                text("SELECT sum(occupied) FROM evaluation_execution_pools")
            )
            == 0
        )


async def test_start_redelivery_returns_original_before_new_preflight(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    _, scope, principal, _, _, _, factory = budget_binding_fixture
    service, _, batch = await scheduled_batch(budget_binding_fixture)
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        payload = await work.db_session.scalar(
            text("SELECT payload FROM evaluation_batch_commands WHERE kind='start'")
        )

    def unavailable_preflight(_):
        raise AssertionError("existing durable command must not request new preflight")

    service.preflight_factory = unavailable_preflight
    assert (await service.start(scope, principal, "start-scheduler", payload)).id == batch.id
    with pytest.raises(ValueError, match="request_conflict"):
        await service.start(
            scope, principal, "start-scheduler", {**payload, "suite_version": str(uuid4())}
        )


@pytest.mark.parametrize("boundary", ["timer", "deadletter", "worker_timeout", "worker_error"])
async def test_durable_timer_wins_against_actual_worker_unknown_outcome(
    budget_binding_fixture, boundary
):
    import asyncio

    from app.application.execution.activity_registry import ActivityRegistry
    from app.application.execution.activity_worker import ActivityWorker
    from app.application.execution.decisions.base import fail_for_activity
    from app.application.execution.run_service import RunService
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_activity_timeout import PostgresActivityTimeoutGuard
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource

    fixture = budget_binding_fixture
    _, scope, _, _, _, _, factory = fixture
    _, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    handler = actual_handler(fixture, scheduler.execution_policy)
    registry = ActivityRegistry()
    entered, release = asyncio.Event(), asyncio.Event()

    class UnknownCall:
        activity_type = "model.call"
        idempotent = False

        async def execute(self, request, context):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            if boundary == "worker_error":
                raise ValueError("post-start ambiguous handler failure")
            raise TimeoutError("post-start ambiguous response")

    registry.register(UnknownCall())
    handler._activity_timeout = PostgresActivityTimeoutGuard(registry)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1):
        return await handler.handle(
            create.model_copy(
                update={
                    "command_id": uuid4(),
                    "command_type": kind,
                    "command_schema_version": version,
                    "expected_stream_version": None,
                    "payload": payload,
                }
            )
        )

    assert (await command("StartRun", {})).status == "accepted"
    activity = uuid4()
    assert (
        await command(
            "RequestActivity",
            {
                "activity_id": str(activity),
                "activity_type": "model.call",
                "timeout_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
                "input_ref": "test/input",
                "input_digest": "a" * 64,
            },
            2,
        )
    ).status == "accepted"
    auth = AuthorizationContext.system("execution-kernel")
    projector = PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=auth
    )
    await projector.run_once(scope, limit=100)
    worker = ActivityWorker(
        store=PostgresActivityStore(session_factory=handler._session_factory, authorization=auth),
        run_contexts=PostgresRunContextSource(
            session_factory=handler._session_factory, authorization=auth
        ),
        run_service=RunService(orchestrator=handler),
        registry=registry,
        worker_id="e06-real-worker",
        execution_gate=handler._evaluation_execution,
    )
    worker_task = asyncio.create_task(worker.run_once(now=datetime.now(UTC), limit=1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if boundary.startswith("worker_"):
            release.set()
            assert (await worker_task).unknown == 1
        else:
            await asyncio.sleep(1.1)
            from app.infrastructure.adapters.execution_ports import SqlAlchemyTimerDispatcher

            if boundary == "timer":
                fired = await SqlAlchemyTimerDispatcher(
                    session_factory=handler._session_factory, authorization=auth
                ).fire_due(limit=10, now=datetime.now(UTC), claim_ttl=timedelta(seconds=30))
                assert fired.fired == 1
            else:
                reclaimed = await PostgresActivityStore(
                    session_factory=handler._session_factory,
                    authorization=auth,
                    max_claim_attempts=1,
                ).claim(
                    now=datetime.now(UTC) + timedelta(minutes=2),
                    limit=1,
                    worker_id="deadletter-recovery",
                    claim_ttl=timedelta(seconds=30),
                )
                assert not reclaimed
            async with factory(auth) as work:
                timer = (
                    (
                        await work.db_session.execute(
                            text(
                                "SELECT * FROM execution_command_inbox WHERE command_type='FailActivity'"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                timeout = CommandEnvelope.model_validate(
                    {k: timer[k] for k in CommandEnvelope.model_fields if k in timer}
                )
            assert (await handler.handle(timeout)).status == "rejected"
            if boundary == "timer":
                await PostgresActivityStore(
                    session_factory=handler._session_factory,
                    authorization=auth,
                    max_claim_attempts=1,
                ).claim(
                    now=datetime.now(UTC) + timedelta(minutes=2),
                    limit=1,
                    worker_id="concurrent-deadletter",
                    claim_ttl=timedelta(seconds=30),
                )
                async with factory(auth) as work:
                    other = (
                        (
                            await work.db_session.execute(
                                text(
                                    "SELECT * FROM execution_command_inbox WHERE command_type='FailActivity' AND payload->>'failure_code'='ACTIVITY_DEAD_LETTERED'"
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    competing = CommandEnvelope.model_validate(
                        {k: other[k] for k in CommandEnvelope.model_fields if k in other}
                    )
                assert (await handler.handle(competing)).status == "rejected"
            async with factory(auth) as work:
                unknown = (
                    (
                        await work.db_session.execute(
                            text(
                                "SELECT * FROM execution_command_inbox WHERE command_type='MarkActivityOutcomeUnknown'"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                envelope = CommandEnvelope.model_validate(
                    {k: unknown[k] for k in CommandEnvelope.model_fields if k in unknown}
                )
            assert (await handler.handle(envelope)).status == "accepted"
    finally:
        release.set()
        results = await asyncio.gather(worker_task, return_exceptions=True)
    assert not isinstance(results[0], BaseException)
    await projector.run_once(scope, limit=100)
    async with factory(auth) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_events WHERE event_type='ActivityFailed'")
            )
            == 0
        )
        projection = await work.evaluation_batch.projection(scope, row)
        state = RunState.model_validate(projection["state"])
        decision = fail_for_activity(state, "unknown", activity_id=activity, max_retries=3)
        assert decision.payload["retryable"] is False
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='RetryRun'")
            )
            == 0
        )


async def test_kernel_cannot_rewrite_frozen_batch_intent_or_history(budget_binding_fixture):
    from sqlalchemy.exc import DBAPIError

    from app.domain.models.authorization import AuthorizationContext

    _, _, _, _, _, _, factory = budget_binding_fixture
    _, scheduler, _ = await scheduled_batch(budget_binding_fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        for sql in (
            "UPDATE evaluation_batch_attempts SET intent='{}'::jsonb",
            "UPDATE evaluation_batch_attempts SET envelope=NULL",
            "UPDATE evaluation_batch_commands SET payload='{}'::jsonb",
            "UPDATE evaluation_batch_events SET kind='rewritten'",
            "UPDATE evaluation_batches SET principal='{}'::jsonb",
        ):
            with pytest.raises(DBAPIError):
                async with work.db_session.begin_nested():
                    await work.db_session.execute(text(sql))


async def test_prepared_input_survives_sink_rollback_without_readmitting(
    budget_binding_fixture, monkeypatch
):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    _, scope, _, _, _, _, factory = budget_binding_fixture
    _, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    original = DBEvaluationBatchRepository.submitted

    async def crash(*args, **kwargs):
        raise ConnectionError("sink rolled back before inbox commit")

    monkeypatch.setattr(DBEvaluationBatchRepository, "submitted", crash)
    with pytest.raises(ConnectionError, match="sink rolled back"):
        await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert row["prepared_envelope"] is not None
        frozen = row["prepared_envelope"]
        assert row["envelope"] is None
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_command_inbox")) == 0
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_execution_leases"))
            == 0
        )
    monkeypatch.setattr(DBEvaluationBatchRepository, "submitted", original)

    class UnavailableHeads:
        async def active_execution(self, **kwargs):
            raise AssertionError("prepared command must not be freshly admitted")

    scheduler.admission._policy_heads = UnavailableHeads()
    assert (await scheduler.tick(datetime.now(UTC))).dispatched == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert row["envelope"] == frozen
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_configurations")) == 1
        )


async def test_scoring_candidates_require_actual_success_and_keep_batch_running(
    budget_binding_fixture,
):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    fixture = budget_binding_fixture
    _, scope, principal, suite, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_batch.scoring_candidates(scope, batch.id) == []
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
    handler = actual_handler(fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"
    for kind in ("StartRun", "CompleteRun"):
        assert (
            await handler.handle(
                create.model_copy(
                    update={
                        "command_id": uuid4(),
                        "command_type": kind,
                        "expected_stream_version": None,
                        "payload": {},
                    }
                )
            )
        ).status == "accepted"
    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    assert (await service.get(scope, principal, batch.id)).status == "running"
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        candidates = await work.evaluation_batch.scoring_candidates(scope, batch.id)
        assert len(candidates) == 1
        assert candidates[0].run_id == row["run_id"]
        assert candidates[0].suite_version_id == suite.id
        assert candidates[0].run_revision > 0
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "scoring_status"
        ] == "pending"


async def test_batch_deadline_expiring_during_input_preparation_fences_sink(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    _, scope, _, _, _, _, factory = budget_binding_fixture
    _, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    original = scheduler.admission._objects.put_input

    async def expires_during_prepare(run, body):
        saved = await original(run, body)
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            await work.db_session.execute(
                text(
                    "UPDATE evaluation_batches SET deadline=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                {"id": batch.id},
            )
            await work.commit()
        return saved

    scheduler.admission._objects.put_input = expires_during_prepare
    assert (await scheduler.tick(datetime.now(UTC))).dispatched == 0
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_command_inbox")) == 0
        )
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert row["prepared_envelope"] is not None
        assert row["envelope"] is None


async def test_required_human_review_is_persisted_separately(budget_binding_fixture):
    from app.domain.evaluation.configuration import SuiteDefinition
    from app.domain.evaluation.rubric import RubricDefinition

    suites, scope, principal, suite, _, _, _ = budget_binding_fixture
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    definition = {field: getattr(rubric, field) for field in RubricDefinition.model_fields}
    definition["required_conditions"] = [
        {"dimension_id": "correctness", "minimum": 3, "source": "human"}
    ]
    draft = await suites.create(
        scope,
        principal,
        kind="rubric",
        name="human required",
        definition=RubricDefinition.model_validate(definition).model_dump(mode="json"),
        request_id="human-rubric",
    )
    published = await suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=draft.id,
        expected_revision=1,
        request_id="human-rubric-publish",
    )
    definition = {field: getattr(suite, field) for field in SuiteDefinition.model_fields}
    definition["rubric_version"] = published.id
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="review required",
        definition=SuiteDefinition.model_validate(definition).model_dump(mode="json"),
        request_id="human-suite",
    )
    published_suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id="human-suite-publish",
    )
    service, scheduler, batch = await scheduled_batch(
        (*budget_binding_fixture[:3], published_suite, *budget_binding_fixture[4:])
    )
    await scheduler.tick(datetime.now(UTC))
    assert (await service.get(scope, principal, batch.id)).review_status == "pending"


async def test_retry_failed_creates_new_lineage_and_rechecks_current_requester(
    budget_binding_fixture,
):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from tests.app.execution_test_support import execution_admin_session

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, parent = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, parent.id))[0]
    handler = actual_handler(fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"
    for kind, payload in (
        ("StartRun", {}),
        ("FailRun", {"failure_code": "ACTIVITY_HANDLER_ERROR", "retryable": False}),
    ):
        assert (
            await handler.handle(
                create.model_copy(
                    update={
                        "command_id": uuid4(),
                        "command_type": kind,
                        "expected_stream_version": None,
                        "payload": payload,
                    }
                )
            )
        ).status == "accepted"
    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    payload = {"batch_id": str(parent.id)}
    child = await service.retry_failed(scope, principal, "manual-retry", payload)
    assert child.id != parent.id
    assert (await service.retry_failed(scope, principal, "manual-retry", payload)).id == child.id
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        saved = await work.evaluation_batch.get(scope, child.id)
        assert saved["parent_batch"] == parent.id
        assert saved["selected_slots"] == [str(row["id"])]
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_attempts"))
            == 1
        )
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        child_row = (await work.evaluation_batch.results(scope, child.id))[0]
        assert child_row["run_id"] != row["run_id"]
        assert child_row["attempt"] == 0
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    with pytest.raises(PermissionError):
        await service.retry_failed(scope, principal, "manual-retry", payload)
    with pytest.raises(PermissionError):
        await service.get(scope, principal, parent.id)


async def test_kernel_scheduler_factory_admits_through_actual_shared_ports(
    budget_binding_fixture, monkeypatch
):
    from types import SimpleNamespace

    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.composition import evaluation
    from app.composition.evaluation import build_batch_scheduler

    suites, _, _, _, _, _, factory = budget_binding_fixture
    _, configured, _ = await scheduled_batch(budget_binding_fixture)
    monkeypatch.setattr(evaluation, "build_suite_service", lambda **kwargs: suites)
    monkeypatch.setattr(
        evaluation,
        "build_environment_service",
        lambda **kwargs: SimpleNamespace(registry=AdapterRegistry(), ceiling=2),
    )
    settings = SimpleNamespace(
        evaluation_execution_policy_revision=1,
        evaluation_subject_concurrency=5,
        evaluation_judge_concurrency=2,
        evaluation_execution_global_limit=None,
        evaluation_execution_user_limit=None,
    )
    scheduler = build_batch_scheduler(
        settings=settings,
        resources=None,
        shared=SimpleNamespace(uow_factory=factory, run_admission_service=configured.admission),
    )
    assert (await scheduler.tick(datetime.now(UTC))).dispatched == 1


async def test_api_role_cannot_insert_kernel_state_or_read_other_scope(budget_binding_fixture):
    from sqlalchemy.exc import DBAPIError

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope

    suites, scope, principal, _, _, _, _ = budget_binding_fixture
    _, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    await scheduler.tick(datetime.now(UTC))
    async with suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batches")) == 1
        with pytest.raises(DBAPIError):
            async with work.db_session.begin_nested():
                await work.db_session.execute(
                    text(
                        "INSERT INTO evaluation_batches(id,suite_version,principal,scope_body,owner_user_id,created_by,status) SELECT :id,suite_version,principal,scope_body,owner_user_id,created_by,'completed' FROM evaluation_batches"
                    ),
                    {"id": uuid4()},
                )
        with pytest.raises(DBAPIError):
            async with work.db_session.begin_nested():
                await work.db_session.execute(
                    text("UPDATE evaluation_batch_results SET execution_status='succeeded'")
                )
    other = principal.model_copy(update={"user_id": str(uuid4())})
    other_scope = OwnerScope.personal(other.user_id)
    async with suites.uow_factory(
        AuthorizationContext.for_principal(other, scope=other_scope)
    ) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_batches WHERE id=:id"), {"id": batch.id}
            )
            == 0
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_batch_results")) == 0
        )


async def ready_pending_environment(fixture):
    from app.domain.evaluation.environment import transition
    from app.domain.models.authorization import AuthorizationContext

    _, scope, _, _, _, _, factory = fixture
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        identities = (
            (
                await work.db_session.execute(
                    text("SELECT id FROM evaluation_environment_leases WHERE state='preparing'")
                )
            )
            .scalars()
            .all()
        )
        assert len(identities) == 1
        lease = await work.evaluation_environment.lease(scope, identities[0])
        await work.evaluation_environment.save(scope, lease, transition(lease, "ready"))
        await work.commit()


async def complete_pending_environment_cleanup(fixture):
    from app.domain.models.authorization import AuthorizationContext

    _, scope, _, _, _, _, factory = fixture
    completed = []
    for phase, evidence in (("cleanup", {}), ("verify_clean", {"verified": True, "resources": []})):
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            identity = await work.db_session.scalar(
                text(
                    "SELECT id FROM evaluation_environment_operations WHERE phase=:phase AND status='queued'"
                ),
                {"phase": phase},
            )
            assert identity is not None
            operation, _ = await work.evaluation_environment.claim(scope, identity)
            await work.commit()
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            assert await work.evaluation_environment.complete(scope, operation, evidence)
            await work.commit()
        completed.append((operation, evidence))
    return completed


async def late_physical_case(budget_binding_fixture, terminal):
    from app.domain.evaluation.budget import BudgetBucket
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import (
        demand,
        repository,
    )

    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
    handler = actual_handler(fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1):
        result = await handler.handle(
            create.model_copy(
                update={
                    "command_id": uuid4(),
                    "command_type": kind,
                    "command_schema_version": version,
                    "expected_stream_version": None,
                    "payload": payload,
                }
            )
        )
        assert result.status == "accepted"

    await command("StartRun", {})
    activity = uuid4()
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "model.call",
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "input_ref": "test/input",
            "input_digest": "a" * 64,
        },
        2,
    )
    store = PostgresActivityStore(session_factory=handler._session_factory, authorization=auth)
    claim = (
        await store.claim(
            now=datetime.now(UTC),
            limit=1,
            worker_id="late-evidence",
            claim_ttl=timedelta(minutes=5),
        )
    )[0]
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    await command(
        "MarkActivityCallStarted",
        {"activity_id": str(activity), "generation": 0, "claim_generation": claim.claim_generation},
        2,
    )
    value = demand(scope, principal, batch=str(batch.id), tokens=100).model_copy(
        update={
            "policy_revision": 1,
            "buckets": (
                BudgetBucket(key="0:global", slots=10),
                BudgetBucket(key="5:batch:" + str(batch.id), tokens=3000000),
            ),
        }
    )
    async with factory(auth) as work:
        config = await work.db_session.scalar(
            text("SELECT id FROM execution_configurations WHERE run_id=:run LIMIT 1"),
            {"run": row["run_id"]},
        )
        call = await DBExecutionUsageRepository(work.db_session).allocate(
            scope,
            run_id=row["run_id"],
            activity_id=activity,
            generation=0,
            claim_generation=claim.claim_generation,
            configuration_id=config,
            request_snapshot={"model": "test"},
        )
        await repository(work).reserve(call, value)
        await work.commit()
    # Formal termination and physical settlement are independent durable facts.
    await command(
        "CompleteActivity",
        {
            "activity_id": str(activity),
            "generation": 0,
            "result_ref": "test/result",
            "result_summary": "finished",
        },
    )
    await command(
        "CompleteRun" if terminal == "succeeded" else "FailRun",
        {}
        if terminal == "succeeded"
        else {"failure_code": "ACTIVITY_HANDLER_ERROR", "retryable": False},
    )
    await PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=auth
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    async with factory(auth) as work:
        before = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert before["execution_status"] == terminal
        assert not before["unknown_effect"]
        if terminal == "succeeded":
            assert await work.evaluation_batch.scoring_candidates(scope, batch.id) == []
        # A still-unsettled send is unsafe even before a callback labels it unknown.
        assert await work.evaluation_batch.unknown_effect(
            scope, row["run_id"], include_unresolved=True
        )
        await repository(work).mark_unknown(call, value)
        await work.commit()
    return service, scheduler, batch, before, call, value


@pytest.mark.parametrize("terminal", ["failed", "succeeded"])
async def test_late_physical_unknown_without_run_revision_blocks_retry_and_scoring(
    budget_binding_fixture, terminal
):
    from app.domain.models.authorization import AuthorizationContext

    _, scope, principal, _, _, _, factory = budget_binding_fixture
    service, scheduler, batch, before, call, value = await late_physical_case(
        budget_binding_fixture, terminal
    )
    row = before
    auth = AuthorizationContext.system("execution-kernel")
    # Revalidate the real facts even before the scheduler has observed the callback.
    if terminal == "failed":
        with pytest.raises(ValueError, match="no_retryable_results"):
            await service.retry_failed(
                scope, principal, "late-unknown-retry", {"batch_id": str(batch.id)}
            )
    else:
        async with factory(auth) as work:
            assert await work.evaluation_batch.scoring_candidates(scope, batch.id) == []
    assert (await scheduler.tick(datetime.now(UTC))).claimed == 1
    async with factory(auth) as work:
        after = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert after["run_revision"] == before["run_revision"]
        assert after["execution_status"] == terminal
        assert after["unknown_effect"]
        assert after["revision"] > before["revision"]
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError, match="evaluation_result_history_immutable"):
            async with work.db_session.begin_nested():
                await work.db_session.execute(
                    text("UPDATE evaluation_batch_results SET unknown_effect=false WHERE id=:id"),
                    {"id": row["id"]},
                )
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == (
            "completed_with_errors" if terminal == "failed" else "running"
        )

    from app.domain.evaluation.budget import BudgetSettlement
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import repository

    async with factory(auth) as work:
        await repository(work).settle(
            call, value, BudgetSettlement(tokens=0, evidence="late-known-usage")
        )
        from app.domain.models.execution_usage import PriceSnapshot

        price = PriceSnapshot.model_validate(
            await work.db_session.scalar(
                text(
                    "SELECT c.body->'price' FROM execution_configurations c JOIN execution_model_dispatches d ON d.configuration_id=c.id AND d.scope_key=c.scope_key WHERE d.call_identity=:call"
                ),
                {"call": call},
            )
        )
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        cost = price.cost(usage)
        await DBExecutionUsageRepository(work.db_session).record(
            scope,
            call,
            {
                "call_identity": call,
                "usage": usage,
                "price_revision": price.revision,
                "cost_usd": str(cost) if cost is not None else None,
            },
        )
        assert await work.evaluation_batch.unknown_effect(
            scope, row["run_id"], include_unresolved=True
        )
        await work.commit()


async def test_effect_read_function_is_scoped_signed_current_and_boolean_only(
    budget_binding_fixture,
):
    import json

    from sqlalchemy.exc import DBAPIError

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    suites, scope, principal, _, _, _, _factory = budget_binding_fixture
    _, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    await scheduler.tick(datetime.now(UTC))
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    query = text(
        "SELECT public.opencitadel_e06_effect_unsafe(:scope,:run,true,:encoded,:signature)"
    )
    async with suites.uow_factory(auth) as work:
        privileges = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT current_user,session_user,has_table_privilege(current_user,'evaluation_run_lineages','SELECT') AS can_read"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert not privileges["can_read"], dict(privileges)
        function = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT p.prosecdef,owner.rolname,owner.rolsuper,owner.rolbypassrls,p.proowner=t.relowner AS same_owner,p.proconfig FROM pg_proc p JOIN pg_roles owner ON owner.oid=p.proowner JOIN pg_class t ON t.oid='evaluation_run_lineages'::regclass WHERE p.oid='public.opencitadel_e06_effect_unsafe(text,uuid,boolean,text,text)'::regprocedure"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert function["prosecdef"], dict(function)
        assert function["same_owner"], dict(function)
        assert function["proconfig"] == ["search_path=pg_catalog"], dict(function)
        assert not await work.db_session.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_proc p, LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE p.oid='public.opencitadel_e06_effect_unsafe(text,uuid,boolean,text,text)'::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')"
            )
        )
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        sealed = await DBPhysicalRequesterRepository(
            work.db_session,
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        ).capture(scope, auth, run_id=row["run_id"])
        values = {
            "scope": "user:" + scope.user_id,
            "run": row["run_id"],
            "encoded": json.dumps(sealed["proof"], sort_keys=True, separators=(",", ":")),
            "signature": sealed["signature"],
        }
        assert await work.db_session.scalar(query, values) is False
        assert (
            await work.evaluation_batch.unknown_effect(
                scope, row["run_id"], include_unresolved=True, principal=principal
            )
            is False
        )
        for override in (
            {"scope": "user:other"},
            {"run": uuid4()},
            {"signature": "0" * 64},
            {"encoded": None, "signature": None},
        ):
            with pytest.raises(DBAPIError, match="evaluation_effect_authorization_invalid"):
                async with work.db_session.begin_nested():
                    await work.db_session.scalar(query, {**values, **override})
        arbitrary = uuid4()
        arbitrary_proof = await DBPhysicalRequesterRepository(
            work.db_session,
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        ).capture(scope, auth, run_id=arbitrary)
        with pytest.raises(DBAPIError, match="evaluation_effect_association_unavailable"):
            async with work.db_session.begin_nested():
                await work.db_session.scalar(
                    query,
                    {
                        **values,
                        "run": arbitrary,
                        "encoded": json.dumps(
                            arbitrary_proof["proof"], sort_keys=True, separators=(",", ":")
                        ),
                        "signature": arbitrary_proof["signature"],
                    },
                )

        async def unsigned_read():
            async with work.db_session.begin_nested():
                await work.db_session.execute(
                    text("SELECT set_config('app.auth_signature','',true)")
                )
                return await work.db_session.scalar(query, values)

        with pytest.raises(DBAPIError, match="evaluation_effect_authorization_invalid"):
            await unsigned_read()
        for table in ("evaluation_run_lineages", "evaluation_execution_leases"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with work.db_session.begin_nested():
                    await work.db_session.execute(text("SELECT * FROM " + table))
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    async with suites.uow_factory(auth) as work:
        with pytest.raises(DBAPIError, match="evaluation_effect_requester_revoked"):
            async with work.db_session.begin_nested():
                await work.db_session.scalar(query, values)


async def test_effect_read_tracks_parent_ancestry_and_denies_missing_or_cyclic_history(
    budget_binding_fixture,
):
    from app.domain.evaluation.batch import schedule_slots
    from app.domain.models.authorization import AuthorizationContext

    _, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, scheduler, parent, before, _, _ = await late_physical_case(budget_binding_fixture, "failed")
    # A persisted manual intent can precede observation (or arrive via API-role
    # intent privileges). Kernel safety must not trust its cached false flag.
    for ancestry in (parent.id, uuid4(), "self"):
        child = uuid4()
        parent_id = child if ancestry == "self" else ancestry
        async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
            await work.evaluation_batch.submit(
                scope,
                principal,
                "retry_failed",
                str(child),
                {
                    "suite_version": str(suite.id),
                    "parent_batch": str(parent_id),
                    "selected_slots": [str(before["id"])],
                    "request_payload": {"batch_id": str(parent_id)},
                },
                child,
            )
            await work.commit()
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            claim = await work.evaluation_batch.claim(datetime.now(UTC))
            assert claim["id"] == child
            await work.evaluation_batch.materialize(
                claim,
                schedule_slots([before["case_revision_id"]], [before["config_version_id"]], 1, 1),
                suite.settings.model_dump(mode="json"),
            )
            row = (await work.evaluation_batch.results(scope, child))[0]
            assert not row["unknown_effect"]
            assert await work.evaluation_batch.unknown_effect(
                scope, row["run_id"], include_unresolved=True
            )
            await work.evaluation_batch.release(claim)
            await work.commit()
        # Actual dispatch final checks reject inherited unknown effects.
        for _ in range(3):
            assert (await scheduler.tick(datetime.now(UTC))).dispatched == 0
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                if (await work.evaluation_batch.get(scope, child))[
                    "status"
                ] == "completed_with_errors":
                    break
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            row = (await work.evaluation_batch.results(scope, child))[0]
            assert row["unknown_effect"]
            assert row["envelope"] is None
            assert (await work.evaluation_batch.get(scope, child))[
                "status"
            ] == "completed_with_errors"


async def test_effect_read_authorizes_current_team_member_not_only_original_requester(
    budget_binding_fixture,
):
    from app.domain.evaluation.batch import schedule_slots
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import execution_admin_session

    suites, _, original, suite, _, _, factory = budget_binding_fixture
    reader_id, _ = await seed()
    team = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        for user in (original.user_id, reader_id):
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
                {"team": team, "user": user},
            )
        await db.commit()
    owner = Principal.model_validate({**original.model_dump(), "team_roles": {team: "member"}})
    scope = OwnerScope.team(original.user_id, team)
    batch = uuid4()
    async with factory(AuthorizationContext.for_principal(owner, scope=scope)) as work:
        await work.evaluation_batch.submit(
            scope, owner, "start", str(batch), {"suite_version": str(suite.id)}, batch
        )
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        claim = await work.evaluation_batch.claim(datetime.now(UTC))
        await work.evaluation_batch.materialize(
            claim,
            schedule_slots([uuid4()], [uuid4()], 1, 1),
            suite.settings.model_dump(mode="json"),
        )
        row = (await work.evaluation_batch.results(scope, batch))[0]
        await work.commit()
    reader = Principal(user_id=reader_id, team_roles={team: "member"})
    reader_scope = OwnerScope.team(reader_id, team)
    async with suites.uow_factory(
        AuthorizationContext.for_principal(reader, scope=reader_scope)
    ) as work:
        assert (
            await work.evaluation_batch.unknown_effect(
                reader_scope, row["run_id"], include_unresolved=True, principal=reader
            )
            is False
        )
