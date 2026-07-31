#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.models.tool_approval import (
    ApprovalCallInput,
    ApprovalStatus,
    ToolApprovalBatch,
)
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.models.codebase import SessionMode
from app.domain.services.agents.tool_batch_executor import ToolBatchExecutor
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.infrastructure.models.resource_governance import (
    ToolApprovalBatchORM,
    ToolApprovalCallORM,
)
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _AsyncSessionAdapter:
    """Only adapts SQLAlchemy's local sync Session call shape to AsyncSession."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self) -> None:
        self._session.flush()


@pytest.fixture
def repo_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'approval-repository.db'}")
    ToolApprovalBatchORM.__table__.create(engine)
    ToolApprovalCallORM.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        repo = DBResourceGovernanceRepository(_AsyncSessionAdapter(session))
        yield SimpleNamespace(repo=repo, session=session, engine=engine)
        session.rollback()
    engine.dispose()


@pytest.fixture
def repo(repo_db):
    return repo_db.repo


def _policy() -> ToolExecutionPolicy:
    return ToolExecutionPolicy(
        capability=ToolCapability.EXECUTION,
        effect=ToolEffect.EXTERNAL_WRITE,
        idempotency=ToolIdempotency.IDEMPOTENT_WITH_KEY,
        approval=ApprovalMode.ALWAYS,
        concurrency_group="browser",
    )


_UNGATED_WRITE_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.NEVER,
    concurrency_group="filesystem",
)


class _RepositoryInspectionTool(BaseTool):
    name = "repository-inspection"

    @tool(
        name="write_file",
        description="write",
        parameters={"path": {"type": "string"}},
        required=["path"],
        policy=_UNGATED_WRITE_POLICY,
    )
    async def write_file(self, path: str) -> str:
        return path


@pytest.mark.asyncio
async def test_approval_batch_preserves_all_calls_and_policy_metadata(repo):
    batch = ToolApprovalBatch.for_calls(
        session_id="s1",
        calls=[
            ApprovalCallInput(
                "tc1", "browser_click", {"selector": "#buy"}, 0, policy=_policy()
            ),
            ApprovalCallInput(
                "tc2", "send_message", {"channel": "ops"}, 1, policy=_policy()
            ),
        ],
    )

    await repo.save_approval_batch(batch)
    loaded = await repo.get_pending_approval_batch("s1")

    assert loaded is not None
    assert [call.tool_call_id for call in loaded.calls] == ["tc1", "tc2"]
    assert loaded.calls[0].normalized_args == {"selector": "#buy"}
    assert loaded.calls[0].args_hash == (
        "97e7b547ae8a08634cea48f6e5fff6da4bbd1fceba8c5c51d4915771b24cd572"
    )
    assert loaded.calls[0].capability == ToolCapability.EXECUTION
    assert loaded.calls[0].effect == ToolEffect.EXTERNAL_WRITE
    assert loaded.calls[0].idempotency == ToolIdempotency.IDEMPOTENT_WITH_KEY
    assert loaded.calls[0].approval == ApprovalMode.ALWAYS
    assert loaded.calls[0].concurrency_group == "browser"


@pytest.mark.asyncio
async def test_approval_decision_is_idempotent(repo):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo.save_approval_batch(batch)

    first = await repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")
    second = await repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")

    assert first is not None
    assert second is not None
    assert first.decided_at == second.decided_at
    assert second.decided_by == "u1"


@pytest.mark.asyncio
async def test_conflicting_second_decision_cannot_overwrite_first(repo):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo.save_approval_batch(batch)
    approved = await repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )

    with pytest.raises(ValueError, match="already decided"):
        await repo.decide_approval_call("tc1", ApprovalStatus.REJECTED, "u2")

    assert approved is not None
    assert approved.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_decision_refreshes_call_state_after_acquiring_batch_lock(repo_db):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo_db.repo.save_approval_batch(batch)
    repo_db.session.commit()
    preloaded = repo_db.session.execute(
        select(ToolApprovalCallORM).where(
            ToolApprovalCallORM.tool_call_id == "tc1"
        )
    ).scalar_one()
    assert preloaded.status == ApprovalStatus.PENDING.value

    externally_decided_at = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    with Session(repo_db.engine) as concurrent_session:
        concurrent_session.execute(
            update(ToolApprovalCallORM)
            .where(ToolApprovalCallORM.tool_call_id == "tc1")
            .values(
                status=ApprovalStatus.APPROVED.value,
                decided_by="u-concurrent",
                decided_at=externally_decided_at,
            )
        )
        concurrent_session.commit()

    with pytest.raises(ValueError, match="already decided"):
        await repo_db.repo.decide_approval_call(
            "tc1", ApprovalStatus.REJECTED, "u-waiter"
        )

    assert preloaded.status == ApprovalStatus.APPROVED.value
    assert preloaded.decided_by == "u-concurrent"
    assert preloaded.decided_at == externally_decided_at.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_expired_batch_cannot_be_loaded_or_approved(repo):
    batch = ToolApprovalBatch.for_calls(
        "s1",
        [ApprovalCallInput("tc1", "browser_click", {}, 0)],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    await repo.save_approval_batch(batch)

    assert await repo.get_pending_approval_batch("s1") is None
    expired = await repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )

    assert expired is not None
    assert expired.status == ApprovalStatus.EXPIRED
    assert expired.decided_by is None


@pytest.mark.asyncio
async def test_expired_newest_batch_does_not_hide_older_pending_batch(repo):
    now = datetime.now(timezone.utc)
    older = ToolApprovalBatch.for_calls(
        "s1",
        [ApprovalCallInput("tc1", "browser_click", {}, 0)],
        expires_at=now + timedelta(minutes=5),
    )
    older.created_at = now - timedelta(minutes=2)
    newer = ToolApprovalBatch.for_calls(
        "s1",
        [ApprovalCallInput("tc2", "send_message", {}, 0)],
        expires_at=now - timedelta(seconds=1),
    )
    newer.created_at = now - timedelta(minutes=1)
    await repo.save_approval_batch(older)
    await repo.save_approval_batch(newer)

    loaded = await repo.get_pending_approval_batch("s1")

    assert loaded is not None
    assert loaded.id == older.id


@pytest.mark.asyncio
async def test_approval_batch_consumption_is_idempotent(repo):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo.save_approval_batch(batch)
    await repo.decide_approval_call("tc1", ApprovalStatus.APPROVED, "u1")

    first = await repo.consume_approval_batch(batch.id)
    second = await repo.consume_approval_batch(batch.id)

    assert first is not None
    assert second is not None
    assert first.status == ApprovalStatus.CONSUMED
    assert second.status == ApprovalStatus.CONSUMED
    assert first.execution_claimed is True
    assert second.execution_claimed is False
    assert first.decided_at == second.decided_at
    assert [call.status for call in second.calls] == [ApprovalStatus.APPROVED]


@pytest.mark.asyncio
async def test_executor_persists_and_claims_ungated_write_before_invocation(
    repo_db,
):
    invocation_states = []

    async def invoke(tool_pack, function_name, arguments):
        record = repo_db.session.get(ToolApprovalBatchORM, batch.batch_id)
        invocation_states.append(record.status)
        return await tool_pack.invoke(function_name, **arguments)

    executor = ToolBatchExecutor(
        session_id="s1",
        tools=[_RepositoryInspectionTool()],
        approval_repository=repo_db.repo,
        capability_policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        invoke_call=invoke,
    )
    batch = await executor.preflight([
        {
            "id": "tc-write",
            "function": {
                "name": "write_file",
                "arguments": {"path": "one"},
            },
        }
    ])

    persisted = repo_db.session.get(ToolApprovalBatchORM, batch.batch_id)
    assert persisted is not None
    assert persisted.status == ApprovalStatus.APPROVED.value
    assert invocation_states == []

    result = await executor.execute(batch)

    assert result.rejected_reason is None
    assert invocation_states == [ApprovalStatus.CONSUMED.value]


@pytest.mark.asyncio
async def test_identical_decision_retry_after_consumption_preserves_call(repo):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo.save_approval_batch(batch)
    decided = await repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )
    await repo.consume_approval_batch(batch.id)

    retried = await repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )

    assert decided is not None
    assert retried is not None
    assert retried.status == ApprovalStatus.APPROVED
    assert retried.decided_by == decided.decided_by == "u1"
    assert retried.decided_at == decided.decided_at


@pytest.mark.asyncio
async def test_identical_decision_retry_after_batch_expiry_preserves_call(repo_db):
    batch = ToolApprovalBatch.for_calls(
        "s1", [ApprovalCallInput("tc1", "browser_click", {}, 0)]
    )
    await repo_db.repo.save_approval_batch(batch)
    decided = await repo_db.repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )
    batch_record = repo_db.session.get(ToolApprovalBatchORM, batch.id)
    assert batch_record is not None
    batch_record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    repo_db.session.flush()

    retried = await repo_db.repo.decide_approval_call(
        "tc1", ApprovalStatus.APPROVED, "u1"
    )

    assert decided is not None
    assert retried is not None
    assert retried.status == ApprovalStatus.APPROVED
    assert retried.decided_by == decided.decided_by == "u1"
    assert retried.decided_at == decided.decided_at


def test_explicit_expiry_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        ToolApprovalBatch.for_calls(
            "s1",
            [ApprovalCallInput("tc1", "browser_click", {}, 0)],
            expires_at=datetime(2026, 7, 28, 12, 0),
        )


def test_db_uow_exposes_resource_governance_repository_contract():
    assert "resource_governance" in DBUnitOfWork.__annotations__
