"""Real projection boundary for Activity owning-Run context."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import RuntimePolicyIntegrityError
from app.infrastructure.execution.models import ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_run_context_source import (
    PostgresRunContextSource,
)
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_source_verifies_state_hash_policy_metadata_and_owner_scope() -> None:
    run_id = uuid4()
    correlation_id = uuid4()
    now = datetime(2026, 8, 26, tzinfo=UTC)
    snapshot_json = run_policy_snapshot_json(RunFamily.AGENT)
    state = RunState(
        run_id=run_id,
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={},
        policy_snapshot=snapshot_json,
        status=RunStatus.RUNNING,
        stream_version=2,
        owner_user_id="context-user",
        correlation_id=correlation_id,
    )
    state_json = state.model_dump(mode="json")
    async with execution_admin_session() as session:
        session.add(
            ExecutionRunProjectionORM(
                run_id=run_id,
                family=RunFamily.AGENT.value,
                source_entity_type="session",
                source_entity_id="session-1",
                execution_policy_revision_id=snapshot_json["execution_revision_id"],
                execution_policy_digest=snapshot_json["execution_policy_digest"],
                status=RunStatus.RUNNING.value,
                terminal=False,
                parent_run_id=None,
                correlation_id=correlation_id,
                owner_user_id="context-user",
                team_id=None,
                stream_version=2,
                last_event_position=2,
                state=state_json,
                state_hash=canonical_state_hash(state),
                last_event_hash="a" * 64,
                created_at=now,
                updated_at=now,
                terminal_at=None,
            )
        )
        await session.commit()

    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    source = PostgresRunContextSource(
        session_factory=sessions,
        authorization=AuthorizationContext.system("run-context-test"),
    )
    try:
        context = await source.load(run_id)
        assert context.run_id == run_id
        assert context.owner_scope.user_id == "context-user"

        async with execution_admin_session() as session:
            record = await session.get(ExecutionRunProjectionORM, run_id)
            assert record is not None
            record.execution_policy_digest = "sha256:" + "0" * 64
            await session.commit()

        with pytest.raises(RuntimePolicyIntegrityError, match="POLICY_SNAPSHOT_INVALID"):
            await source.load(run_id)
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id == run_id)
            )
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_missing_record_during_rebuild_is_retryable_not_permanent() -> None:
    """K4-1/P2-14: a rebuild-window context miss defers, it never poisons.

    With a ``rebuilding`` marker present, a missing Run projection row raises
    the retryable ``RunContextUnavailableError`` (the activity worker defers);
    without any marker the historical permanent failure is preserved.
    """
    from app.application.execution.run_context import RunContextUnavailableError
    from app.infrastructure.execution.models import ExecutionPoisonedScopeORM

    missing_run_id = uuid4()
    scope_key = f"user:rebuild-user-{uuid4()}"
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    source = PostgresRunContextSource(
        session_factory=sessions,
        authorization=AuthorizationContext.system("run-context-rebuild-test"),
    )
    try:
        # No rebuild in flight -> permanent policy failure, as before.
        with pytest.raises(RuntimePolicyIntegrityError, match="POLICY_SNAPSHOT_INVALID"):
            await source.load(missing_run_id)

        async with execution_admin_session() as session:
            session.add(
                ExecutionPoisonedScopeORM(
                    owner_scope_key=scope_key,
                    owner_user_id=scope_key.removeprefix("user:"),
                    team_id=None,
                    reason="rebuilding",
                    last_error="operator-driven projection rebuild in flight",
                    failure_count=0,
                    rebuilding=True,
                )
            )
            await session.commit()

        with pytest.raises(RunContextUnavailableError):
            await source.load(missing_run_id)
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPoisonedScopeORM).where(
                    ExecutionPoisonedScopeORM.owner_scope_key == scope_key
                )
            )
            await session.commit()
