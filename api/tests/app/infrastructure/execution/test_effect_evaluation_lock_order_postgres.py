# ruff: noqa: F401,F811 -- real published evaluation fixtures
"""Effect transactions obey the same budget-before-scope order as ordinary Runs."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.effect_service import EffectService, command_envelope
from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
from app.domain.execution.commands import ActivityLeaseProof
from app.domain.execution.effect import EffectOperationSpec
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.execution.models import ExecutionActivityTaskORM
from app.infrastructure.execution.postgres_effect_store import PostgresEffectStore, authorize_actor
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.repositories.db_evaluation_execution_repository import (
    DBEvaluationExecutionRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
)
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


@pytest.mark.parametrize("path", ["preflight", "finish_request"])
async def test_effect_budget_lock_does_not_invert_scope_lock_and_updates_accepted_lease(
    budget_binding_fixture, isolated_database, path
):
    fixture = budget_binding_fixture
    _, scope, principal, _, _, _, original_uow = fixture
    binding, snapshot = await bound_run(fixture)
    settings = load_deployment_settings()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=isolated_database[0].url.database),
        pool_size=4,
        max_overflow=0,
    )
    sessions = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    auth = AuthorizationContext.system("execution-kernel")
    policy = ExecutionSlotPolicy(revision=1)
    entered = asyncio.Event()
    contender = {"command_id": None, "pid": None}

    class ObservedGuard(EvaluationExecutionGuard):
        async def lock_command(self, session, command):
            if command.command_id == contender["command_id"]:
                contender["pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
                entered.set()
            return await super().lock_command(session, command)

    guard = ObservedGuard(policy, session_factory=sessions, authorization=auth)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions,
        aggregates={"run": RunAggregate()},
        authorization=auth,
        evaluation_execution=guard,
    )

    def uow():
        return DBUnitOfWork(
            sessions,
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=auth,
        )

    store = PostgresEffectStore(
        session_factory=sessions, authorization=auth, orchestrator=handler, uow_factory=uow
    )
    data = {}

    async def put(key, value):
        data[key] = value

    async def get(key):
        return data[key]

    service = EffectService(
        store=store,
        objects=ActivityObjectStore(SimpleNamespace(put_bytes=put, get_bytes=get)),
        files=None,
    )
    activity_id, operation_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    proof = ActivityLeaseProof(
        activity_id=activity_id,
        request_generation=0,
        claim_generation=1,
        worker_id="effect-lock-order",
    )
    spec = EffectOperationSpec(
        operation_id=operation_id,
        invocation_id=operation_id,
        run_id=binding.run_id,
        activity_id=activity_id,
        generation=0,
        provider_identity="manual-fixture",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key=f"inv:{operation_id}",
        capability="manual_only",
    )
    context = SimpleNamespace(
        worker_id=proof.worker_id,
        claim_generation=1,
        run=SimpleNamespace(run_id=binding.run_id, owner_scope=scope),
    )
    task = None
    try:
        async with original_uow() as work:
            await DBEvaluationExecutionRepository(work.db_session).prepare(
                scope, binding.run_id, policy
            )
            await work.commit()
        for kind in ("CreateRun", "StartRun"):
            assert (
                await handler.handle(run_command(fixture, binding, snapshot, kind))
            ).status == "accepted"
        requested = run_command(
            fixture,
            binding,
            snapshot,
            "RequestActivity",
            {
                "activity_id": str(activity_id),
                "activity_type": "tool.call",
                "timeout_at": (now + timedelta(hours=1)).isoformat(),
                "input_digest": "a" * 64,
            },
        )
        assert (await handler.handle(requested)).status == "accepted"
        async with store.transaction() as session:
            await session.execute(
                update(ExecutionActivityTaskORM)
                .where(ExecutionActivityTaskORM.activity_id == activity_id)
                .values(
                    status="call_started",
                    claim_generation=1,
                    claimed_by=proof.worker_id,
                    claim_deadline=now + timedelta(minutes=5),
                    call_started_at=now,
                )
            )
        command = command_envelope(
            spec=spec,
            name="PrepareEffectOperation",
            payload={"spec": spec.model_dump(mode="json")},
            context=context,
        )
        if path == "finish_request":
            await store.worker_command(command)
            await store.worker_command(
                command_envelope(
                    spec=spec,
                    name="StartEffectDispatch",
                    context=context,
                    payload={
                        "operation_id": str(operation_id),
                        "expected_revision": 1,
                        "dispatch_proof": proof.model_dump(mode="json"),
                    },
                )
            )
            unknown = await service.persist_observation(
                spec,
                state="unknown",
                source="transport_unknown",
                evidence={"operation_id": str(operation_id), "reason": "response lost"},
            )
            await store.worker_command(
                command_envelope(
                    spec=spec,
                    name="RecordEffectObservation",
                    context=context,
                    payload={
                        "operation_id": str(operation_id),
                        "expected_revision": 2,
                        "observation": unknown.model_dump(mode="json"),
                    },
                )
            )
            command_unknown = run_command(
                fixture,
                binding,
                snapshot,
                "MarkActivityOutcomeUnknown",
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "claim_generation": 1,
                    "failure_code": "EXTERNAL_OUTCOME_UNKNOWN",
                },
            ).model_copy(update={"command_schema_version": 2, "activity_lease_proof": proof})
            assert (await handler.handle(command_unknown)).status == "accepted"
            observed = await service.persist_observation(
                spec,
                state="applied",
                source="provider_receipt",
                evidence={"operation_id": str(operation_id)},
                result={"success": True},
            )
            await store.defer_observation(spec, context, observed)
            for _ in range(5):
                row = await store.claim_request()
                assert row is not None
                if row["kind"] == "provider_observation":
                    break
                await store.finish_request(row, error_code="FIXTURE_PREVIOUS_QUERY")
            assert row["kind"] == "provider_observation"
            command = run_command(
                fixture,
                binding,
                snapshot,
                "ResolveActivityOutcome",
                {
                    "operation_id": str(operation_id),
                    "expected_revision": 3,
                    "request_id": str(row["request_id"]),
                    "observation": observed.model_dump(mode="json"),
                },
            ).model_copy(update={"command_id": row["request_id"]})

        contender["command_id"] = command.command_id

        async def preflight(work):
            await authorize_actor(work.db_session, scope, principal)

        async with store.transaction() as holder:
            # Hold the actual namespace/policy/pool/lease locks, exactly as a
            # normal lifecycle command does before attempting its scope lock.
            await handler.prepare_command_transaction(
                run_command(fixture, binding, snapshot, "CancelRun", {"reason": "lock-holder"}),
                holder,
            )
            task = asyncio.create_task(
                store.worker_command(command, preflight=preflight)
                if path == "preflight"
                else store.finish_request(row, command=command)
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            for _ in range(100):
                waiting = await holder.scalar(
                    text("SELECT pg_backend_pid()=ANY(pg_blocking_pids(:pid))"),
                    {"pid": contender["pid"]},
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting, "contender must reach a real PostgreSQL budget lock wait"
            assert not task.done()
            assert await holder.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": PostgresEventStore._scope_advisory_lock_key(scope.user_id, scope.team_id)},
            ), "effect contender acquired scope before its blocked budget lock"
        outcome = await asyncio.wait_for(task, timeout=5)
        assert outcome.status == "accepted" if path == "preflight" else outcome is True
        async with store.transaction() as session:
            lease = (
                (
                    await session.execute(
                        text(
                            "SELECT phase,accepted_version,state FROM evaluation_execution_leases WHERE run_id=:run"
                        ),
                        {"run": binding.run_id},
                    )
                )
                .mappings()
                .one()
            )
            version = await session.scalar(
                text(
                    "SELECT max(stream_version) FROM execution_events WHERE stream_type='run' AND stream_id=:run"
                ),
                {"run": str(binding.run_id)},
            )
        assert lease["phase"] == "held"
        assert lease["accepted_version"] == version
        assert lease["state"]["effect_operations"][0]["state"] == (
            "prepared" if path == "preflight" else "applied"
        )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
