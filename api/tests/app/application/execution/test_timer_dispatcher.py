from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.timer_dispatcher import TimerDispatcher
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.adapters.execution_ports import SqlAlchemyTimerDispatcher
from app.infrastructure.execution.models import (
    ExecutionCommandInboxORM,
    ExecutionScheduledCommandORM,
)
from app.infrastructure.execution.sqlalchemy_orchestrator import (
    SqlAlchemyExecutionOrchestrator,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)


def command(*, stream_id: str, expected_version: int | None = None) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=uuid4(),
        command_type="CreateRun",
        command_schema_version=1,
        stream_type="run",
        stream_id=stream_id,
        expected_stream_version=expected_version,
        owner_user_id="timer-user",
        team_id=None,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=NOW,
        payload={
            "family": "agent",
            "source_entity_type": "timer",
            "source_entity_id": stream_id,
            "semantic_payload": {},
            "public_input": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )


async def seed_timer(
    session_factory,
    candidate: CommandEnvelope,
    *,
    cancelled: bool = False,
) -> object:
    timer_id = uuid4()
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("timer-test-seed"),
        )
        session.add(
            ExecutionScheduledCommandORM(
                timer_id=timer_id,
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                command_envelope=candidate.model_dump(mode="json"),
                cancellation_event_types=["RunCompleted"],
                cancellation_event_id=uuid4() if cancelled else None,
                owner_user_id=candidate.owner_user_id,
                team_id=candidate.team_id,
                status="cancelled" if cancelled else "pending",
            )
        )
        await session.commit()
    return timer_id


@pytest.fixture
async def timer_database(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    timer_ids: list = []
    command_ids: list = []
    try:
        yield session_factory, timer_ids, command_ids
    finally:
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("timer-test-cleanup"),
            )
            await session.execute(
                delete(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.command_id.in_(command_ids)
                )
            )
            await session.execute(
                delete(ExecutionScheduledCommandORM).where(
                    ExecutionScheduledCommandORM.timer_id.in_(timer_ids)
                )
            )
            await session.commit()
        await engine.dispose()


def dispatcher(session_factory) -> TimerDispatcher:
    return TimerDispatcher(
        dispatcher=SqlAlchemyTimerDispatcher(
            session_factory=session_factory,
            authorization=AuthorizationContext.system("timer-test"),
        ),
    )


@pytest.mark.asyncio
async def test_due_timer_inserts_one_deterministic_command_and_fires_once(
    timer_database,
) -> None:
    session_factory, timer_ids, command_ids = timer_database
    candidate = command(stream_id=str(uuid4()))
    timer_id = await seed_timer(session_factory, candidate)
    deterministic_id = uuid5(NAMESPACE_URL, f"opencitadel:timer:{timer_id}")
    timer_ids.append(timer_id)
    command_ids.append(deterministic_id)
    service = dispatcher(session_factory)

    first = await service.fire_due(limit=10, now=NOW)
    duplicate_scan = await service.fire_due(
        limit=10,
        now=NOW + timedelta(seconds=1),
    )

    assert first.fired == 1
    assert duplicate_scan.claimed == 0
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("timer-verify"),
        )
        inbox = await session.get(ExecutionCommandInboxORM, deterministic_id)
        timer = await session.get(ExecutionScheduledCommandORM, timer_id)
        assert inbox is not None
        assert inbox.command_id == deterministic_id
        assert inbox.status == "received"
        assert timer.status == "fired"


@pytest.mark.asyncio
async def test_cancelled_timer_never_enters_inbox(timer_database) -> None:
    session_factory, timer_ids, command_ids = timer_database
    candidate = command(stream_id=str(uuid4()))
    timer_id = await seed_timer(session_factory, candidate, cancelled=True)
    deterministic_id = uuid5(NAMESPACE_URL, f"opencitadel:timer:{timer_id}")
    timer_ids.append(timer_id)
    command_ids.append(deterministic_id)

    stats = await dispatcher(session_factory).fire_due(limit=10, now=NOW)

    assert stats.claimed == 0
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("timer-cancel-verify"),
        )
        assert await session.get(ExecutionCommandInboxORM, deterministic_id) is None


@pytest.mark.asyncio
async def test_stale_timer_is_durably_rejected_without_events(timer_database) -> None:
    session_factory, timer_ids, command_ids = timer_database
    stream_id = str(uuid4())
    candidate = command(stream_id=stream_id, expected_version=99)
    timer_id = await seed_timer(session_factory, candidate)
    deterministic_id = uuid5(NAMESPACE_URL, f"opencitadel:timer:{timer_id}")
    timer_ids.append(timer_id)
    command_ids.append(deterministic_id)

    fired = await dispatcher(session_factory).fire_due(limit=10, now=NOW)
    deterministic_command = candidate.model_copy(update={"command_id": deterministic_id})
    result = await SqlAlchemyExecutionOrchestrator(
        session_factory=session_factory,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("timer-orchestrator-test"),
    ).handle(deterministic_command)

    assert fired.fired == 1
    assert result.status == "rejected"
    assert result.rejection_code == "EXPECTED_VERSION_CONFLICT"
