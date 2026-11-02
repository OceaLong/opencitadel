"""Caller-owned transactions cannot publish partial commands or reuse old locks."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionCommandInboxORM, ExecutionEventORM
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.execution_database_fixture import isolated_execution_database  # noqa: F401
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("isolated_execution_database")]


@pytest.fixture
async def runtime():
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    auth = AuthorizationContext.system("transaction-proof")
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, authorization=auth, aggregates={"run": RunAggregate()}
    )
    run_id = uuid4()
    command = CommandEnvelope(
        command_id=uuid4(),
        command_type="CreateRun",
        command_schema_version=1,
        stream_type="run",
        stream_id=str(run_id),
        owner_user_id="transaction-proof",
        team_id=None,
        correlation_id=run_id,
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload={
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": str(run_id),
            "semantic_payload": {},
            "public_input": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    try:
        yield sessions, auth, orchestrator, command
    finally:
        await engine.dispose()


async def test_caller_rollback_removes_event_and_inbox_together(runtime):
    sessions, auth, orchestrator, command = runtime
    async with sessions() as session:
        result = await orchestrator.handle_in_session(command, session)
        assert result.status == "accepted"
        async with sessions() as observer:
            await configure_session_authorization(observer, auth)
            assert await observer.scalar(select(func.count()).select_from(ExecutionEventORM)) == 0
            assert await observer.get(ExecutionCommandInboxORM, command.command_id) is None
        await session.rollback()
    async with sessions() as observer:
        await configure_session_authorization(observer, auth)
        assert await observer.scalar(select(func.count()).select_from(ExecutionEventORM)) == 0
        assert await observer.get(ExecutionCommandInboxORM, command.command_id) is None
    assert (await orchestrator.handle(command)).status == "accepted"


async def test_prepared_budget_locks_cannot_cross_transaction_or_identity(runtime):
    sessions, _auth, orchestrator, command = runtime
    async with sessions() as session:
        prepared = await orchestrator.prepare_command_transaction(command, session)
        changed = command.model_copy(update={"command_id": uuid4()})
        with pytest.raises(ValueError, match="different transaction or identity"):
            await orchestrator.handle_in_session(changed, session, prepared=prepared)
        await session.rollback()
        with pytest.raises(ValueError, match="different transaction or identity"):
            await orchestrator.handle_in_session(command, session, prepared=prepared)

    async with sessions() as session:
        with pytest.raises(ValueError, match="different transaction or identity"):
            await orchestrator.handle_in_session(command, session, prepared=prepared)


async def test_duplicate_waits_before_inbox_while_caller_owns_prepared_transaction(runtime):
    sessions, auth, orchestrator, command = runtime
    duplicate = None
    async with sessions() as holder, sessions() as contender:
        prepared = await orchestrator.prepare_command_transaction(command, holder)
        await configure_session_authorization(contender, auth)
        contender_pid = await contender.scalar(text("SELECT pg_backend_pid()"))
        duplicate = asyncio.create_task(orchestrator.handle_in_session(command, contender))
        try:
            for _ in range(100):
                blocked = await holder.scalar(
                    text("SELECT pg_backend_pid()=ANY(pg_blocking_pids(:pid))"),
                    {"pid": contender_pid},
                )
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked
            assert await holder.get(ExecutionCommandInboxORM, command.command_id) is None
            original = await asyncio.wait_for(
                orchestrator.handle_in_session(command, holder, prepared=prepared), timeout=5
            )
            assert original.status == "accepted"
            await holder.commit()
            replayed = await asyncio.wait_for(duplicate, timeout=5)
            assert replayed == original
            await contender.commit()
        finally:
            if not duplicate.done():
                duplicate.cancel()
                await asyncio.gather(duplicate, return_exceptions=True)
    async with sessions() as observer:
        await configure_session_authorization(observer, auth)
        assert await observer.scalar(select(func.count()).select_from(ExecutionEventORM)) == 1
