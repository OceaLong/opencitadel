#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof of approval decision lock ordering."""
import asyncio
from datetime import datetime, timezone
import os
import uuid

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.tool_approval import (
    ApprovalCallInput,
    ApprovalStatus,
    ToolApprovalBatch,
)
from app.infrastructure.models.resource_governance import (
    ToolApprovalBatchORM,
    ToolApprovalCallORM,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


RUN_POSTGRES_INTEGRATION = (
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") == "1"
)


class _LockSignalingSession:
    def __init__(self, session, lock_attempted: asyncio.Event) -> None:
        self._session = session
        self._lock_attempted = lock_attempted

    async def execute(self, statement):
        if getattr(statement, "_for_update_arg", None) is not None:
            self._lock_attempted.set()
        return await self._session.execute(statement)

    async def flush(self) -> None:
        await self._session.flush()


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL lock proof",
)
@pytest.mark.asyncio
async def test_postgres_waiter_refreshes_decision_after_batch_lock(_db_schema):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = f"approval-lock-session-{uuid.uuid4()}"
    tool_call_id = f"approval-lock-call-{uuid.uuid4()}"
    batch = ToolApprovalBatch.for_calls(
        session_id,
        [ApprovalCallInput(tool_call_id, "browser_click", {}, 0)],
    )
    system = AuthorizationContext.system("approval-lock-test")
    waiter_task = None
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            await setup.execute(insert(SessionModel).values(id=session_id))
            await DBResourceGovernanceRepository(setup).save_approval_batch(batch)
            await setup.commit()

        async with session_factory() as blocker, session_factory() as waiter:
            await configure_session_authorization(blocker, system)
            await configure_session_authorization(waiter, system)
            await blocker.execute(
                select(ToolApprovalBatchORM)
                .where(ToolApprovalBatchORM.id == batch.id)
                .with_for_update()
            )

            lock_attempted = asyncio.Event()
            waiter_repo = DBResourceGovernanceRepository(
                _LockSignalingSession(waiter, lock_attempted)
            )
            waiter_task = asyncio.create_task(
                waiter_repo.decide_approval_call(
                    tool_call_id,
                    ApprovalStatus.REJECTED,
                    "u-waiter",
                )
            )
            await asyncio.wait_for(lock_attempted.wait(), timeout=2)

            decided_at = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
            await blocker.execute(
                update(ToolApprovalCallORM)
                .where(
                    ToolApprovalCallORM.tool_call_id == tool_call_id
                )
                .values(
                    status=ApprovalStatus.APPROVED.value,
                    decided_by="u-first",
                    decided_at=decided_at,
                )
            )
            await blocker.commit()

            with pytest.raises(ValueError, match="already decided"):
                await asyncio.wait_for(waiter_task, timeout=2)
            await waiter.rollback()

        async with session_factory() as verification:
            await configure_session_authorization(verification, system)
            stored = (
                await verification.execute(
                    select(ToolApprovalCallORM).where(
                        ToolApprovalCallORM.tool_call_id == tool_call_id
                    )
                )
            ).scalar_one()
            assert stored.status == ApprovalStatus.APPROVED.value
            assert stored.decided_by == "u-first"
    finally:
        if waiter_task is not None and not waiter_task.done():
            waiter_task.cancel()
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(SessionModel).where(SessionModel.id == session_id)
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL lock proof",
)
@pytest.mark.asyncio
async def test_postgres_competing_consumers_receive_exactly_one_claim(_db_schema):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = f"approval-consume-session-{uuid.uuid4()}"
    tool_call_id = f"approval-consume-call-{uuid.uuid4()}"
    batch = ToolApprovalBatch.for_calls(
        session_id,
        [ApprovalCallInput(tool_call_id, "browser_click", {}, 0)],
    )
    system = AuthorizationContext.system("approval-consume-test")
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            await setup.execute(insert(SessionModel).values(id=session_id))
            repo = DBResourceGovernanceRepository(setup)
            await repo.save_approval_batch(batch)
            await repo.decide_approval_call(
                tool_call_id,
                ApprovalStatus.APPROVED,
                "u1",
            )
            await setup.commit()

        async def consume_once():
            async with session_factory() as session:
                await configure_session_authorization(session, system)
                consumed = await DBResourceGovernanceRepository(
                    session
                ).consume_approval_batch(batch.id)
                await session.commit()
                return consumed

        first, second = await asyncio.gather(
            consume_once(),
            consume_once(),
        )

        assert first is not None
        assert second is not None
        assert sorted(
            [first.execution_claimed, second.execution_claimed]
        ) == [False, True]
        assert first.status == second.status == ApprovalStatus.CONSUMED
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(SessionModel).where(SessionModel.id == session_id)
            )
            await cleanup.commit()
        await engine.dispose()
