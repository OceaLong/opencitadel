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
