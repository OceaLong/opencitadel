#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof of the run-epoch terminal CAS."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.event import SessionStatusEvent
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.session_event import SessionEventModel
from app.infrastructure.repositories.db_session_repository import (
    DBSessionRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


RUN_POSTGRES_INTEGRATION = (
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") == "1"
)


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL CAS proof",
)
@pytest.mark.asyncio
async def test_two_postgres_uows_claim_exactly_one_terminal_for_epoch(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = f"run-epoch-cas-{uuid.uuid4()}"
    epoch_id = f"task-1:{uuid.uuid4()}"
    seq_base = 8_000_000_000_000_000_000 + uuid.uuid4().int % 100_000_000
    system = AuthorizationContext.system("run-epoch-cas-test")
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            await setup.execute(insert(SessionModel).values(id=session_id))
            running = SessionStatusEvent(
                id=str(seq_base),
                status="running",
                run_epoch_id=epoch_id,
            )
            assert await DBSessionRepository(
                setup
            ).claim_session_status_event(
                session_id,
                running,
                running.model_dump(mode="json"),
            )
            await setup.commit()

        async def claim(status, seq):
            async with session_factory() as session:
                await configure_session_authorization(session, system)
                event = SessionStatusEvent(
                    id=str(seq),
                    status=status,
                    run_epoch_id=epoch_id,
                )
                accepted = await DBSessionRepository(
                    session
                ).claim_session_status_event(
                    session_id,
                    event,
                    event.model_dump(mode="json"),
                )
                await session.commit()
                return accepted

        accepted = await asyncio.gather(
            claim("failed", seq_base + 1),
            claim("completed", seq_base + 2),
        )

        assert sorted(accepted) == [False, True]
        async with session_factory() as verification:
            await configure_session_authorization(verification, system)
            session = await verification.scalar(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            records = (
                await verification.execute(
                    select(SessionEventModel)
                    .where(SessionEventModel.session_id == session_id)
                    .order_by(SessionEventModel.seq)
                )
            ).scalars().all()
            terminal_statuses = [
                record.payload["status"]
                for record in records
                if record.payload["status"] != "running"
            ]
            assert terminal_statuses in (["failed"], ["completed"])
            assert session.status == terminal_statuses[0]
            assert session.current_run_terminal_status == terminal_statuses[0]
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(SessionModel).where(SessionModel.id == session_id)
            )
            await cleanup.commit()
        await engine.dispose()
