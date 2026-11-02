"""Exact execution approval survives a lagging or absent UI projection."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.errors import ConflictError, NotFoundError
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.execution.tool_contract import ToolApprovalSubject
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.repositories.db_evaluation_recording_repository import (
    DBEvaluationRecordingRepository,
)
from app.infrastructure.repositories.db_execution_usage_repository import DBExecutionUsageRepository
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_committed_exact_approval_is_authoritative_before_projection():
    settings = load_deployment_settings()
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(engine, signing_secret=settings.session_secret)
    api_engine = create_async_engine(settings.sqlalchemy_database_uri)
    api_sessions = authenticated_session_factory(
        api_engine, signing_secret=settings.database_authorization_signing_secret
    )
    authorization = AuthorizationContext.system("execution-kernel")
    run_id, activity_id, approval_id = uuid4(), uuid4(), uuid4()
    owner = f"approval-proof-{uuid4()}"
    scope = OwnerScope.personal(owner)
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=authorization
    )
    subject = ToolApprovalSubject(
        invocation_id=uuid4(),
        originating_activity_id=uuid4(),
        tool_call_id="write-1",
        tool_name="write_target",
        contract_ref=f"execution/inputs/{run_id}/contract.json",
        contract_object_digest="a" * 64,
        contract_digest="b" * 64,
        arguments_digest="c" * 64,
        target_digest="d" * 64,
        scope_digest="e" * 64,
        authorization_epoch="f" * 64,
    )

    async def send(name, payload, *, version=1):
        result = await handler.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=owner,
                team_id=None,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted", result

    try:
        await send(
            "CreateRun",
            {
                "family": "agent",
                "source_entity_type": "approval-proof",
                "source_entity_id": str(run_id),
                "semantic_payload": {},
                "policy_snapshot": run_policy_snapshot_json("agent"),
            },
        )
        await send("StartRun", {})
        await send(
            "RequestApproval",
            {
                "approval_id": str(approval_id),
                "subject_activity_id": str(activity_id),
                "approval_kind": "tool_effect",
                "risk_summary": "Write exact target",
                "subject_label": "write_target",
                "subject": subject.model_dump(mode="json"),
                "subject_digest": subject.digest,
            },
            version=2,
        )
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            repo = DBEvaluationRecordingRepository(session)
            assert not await repo.approved(
                scope, run_id, activity_id, subject_digest=subject.digest
            )
        await send(
            "DecideApproval",
            {
                "approval_id": str(approval_id),
                "decision": "approved",
                "actor_user_id": owner,
                "expected_subject_digest": subject.digest,
            },
            version=2,
        )
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            assert not await session.scalar(
                text("SELECT 1 FROM execution_approval_projection WHERE run_id=:run"),
                {"run": run_id},
            )
            repo = DBEvaluationRecordingRepository(session)
            assert await repo.approved(scope, run_id, activity_id, subject_digest=subject.digest)
            assert not await repo.approved(scope, run_id, activity_id, subject_digest="0" * 64)
            assert not await repo.approved(scope, run_id, activity_id)
            assert not await repo.approved(
                OwnerScope.personal("other-owner"),
                run_id,
                activity_id,
                subject_digest=subject.digest,
            )
        await send(
            "RequestActivity",
            {
                "activity_id": str(activity_id),
                "activity_type": "tool.call",
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "input_digest": "a" * 64,
                "subject_digest": subject.digest,
            },
            version=3,
        )
        async with sessions() as session:
            await configure_session_authorization(session, authorization)
            await session.execute(
                text(
                    "UPDATE execution_activity_tasks SET status='failed',failure_code='TOOL_CONTRACT_CHANGED' WHERE activity_id=:activity"
                ),
                {"activity": activity_id},
            )
            await session.commit()
        user_authorization = AuthorizationContext.for_principal(
            Principal(user_id=owner), scope=scope
        )
        async with api_sessions() as session:
            await configure_session_authorization(session, user_authorization)
            with pytest.raises(DBAPIError):
                await session.execute(text("SELECT internal_payload FROM execution_events LIMIT 1"))
        async with api_sessions() as session:
            await configure_session_authorization(session, user_authorization)
            repo = DBExecutionUsageRepository(session)
            assert await repo.approval_subject(scope, run_id, approval_id) == subject.digest
            await repo.tool_intent_refresh_target(scope, run_id, activity_id, subject.digest)
            with pytest.raises(ConflictError):
                await repo.tool_intent_refresh_target(scope, run_id, activity_id, "0" * 64)
            with pytest.raises(NotFoundError):
                await repo.tool_intent_refresh_target(
                    OwnerScope.personal("other-owner"), run_id, activity_id, subject.digest
                )
            with pytest.raises(ValueError, match="approval is unavailable"):
                await repo.approval_subject(OwnerScope.personal("other-owner"), run_id, approval_id)
    finally:
        await api_engine.dispose()
        await engine.dispose()
