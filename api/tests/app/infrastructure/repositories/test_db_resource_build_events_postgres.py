#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof of per-build sequence serialization."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
)
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


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL lock proof",
)
@pytest.mark.asyncio
async def test_two_postgres_uows_append_without_duplicate_or_gap(_db_schema):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    build_id = f"build-event-lock-{uuid.uuid4()}"
    system = AuthorizationContext.system("build-event-lock-test")
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            await DBResourceGovernanceRepository(setup).add_build(
                ResourceBuild(
                    id=build_id,
                    resource_kind=ResourceKind.CODEBASE,
                    resource_id=f"cb-{uuid.uuid4()}",
                    version_id=f"cbv-{uuid.uuid4()}",
                    command_key=f"reanalyze:{build_id}",
                    created_by="u1",
                )
            )
            await setup.commit()

        async def append_once(phase: str) -> int:
            async with session_factory() as session:
                await configure_session_authorization(session, system)
                seq = await DBResourceGovernanceRepository(
                    session
                ).append_event(
                    build_id,
                    ResourceBuildEvent(
                        build_id=build_id,
                        seq=0,
                        phase=phase,
                        state=BuildState.RUNNING,
                        progress=0.1,
                    ),
                )
                await session.commit()
                return seq

        sequences = await asyncio.gather(
            append_once("parse-a"),
            append_once("parse-b"),
        )

        assert sorted(sequences) == [1, 2]
        async with session_factory() as verification:
            await configure_session_authorization(verification, system)
            rows = (
                await verification.execute(
                    select(ResourceBuildEventORM)
                    .where(ResourceBuildEventORM.build_id == build_id)
                    .order_by(ResourceBuildEventORM.seq)
                )
            ).scalars().all()
            build = await verification.get(ResourceBuildORM, build_id)
            assert [row.seq for row in rows] == [1, 2]
            assert build.last_event_seq == 2
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(ResourceBuildORM).where(
                    ResourceBuildORM.id == build_id
                )
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL lock proof",
)
@pytest.mark.asyncio
async def test_build_row_lock_serializes_only_the_same_build(_db_schema):
    """A held build-A lock blocks A while an independent build-B commits."""
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    build_a = f"build-event-lock-a-{uuid.uuid4()}"
    build_b = f"build-event-lock-b-{uuid.uuid4()}"
    build_ids = (build_a, build_b)
    system = AuthorizationContext.system("build-event-lock-isolation-test")
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            repository = DBResourceGovernanceRepository(setup)
            for build_id in build_ids:
                await repository.add_build(
                    ResourceBuild(
                        id=build_id,
                        resource_kind=ResourceKind.CODEBASE,
                        resource_id=f"cb-{uuid.uuid4()}",
                        version_id=f"cbv-{uuid.uuid4()}",
                        command_key=f"reanalyze:{build_id}",
                        created_by="u1",
                    )
                )
            await setup.commit()

        async def append_once(build_id: str) -> int:
            async with session_factory() as session:
                await configure_session_authorization(session, system)
                seq = await DBResourceGovernanceRepository(
                    session
                ).append_event(
                    build_id,
                    ResourceBuildEvent(
                        build_id=build_id,
                        seq=0,
                        phase="parse",
                        state=BuildState.RUNNING,
                        progress=0.1,
                    ),
                )
                await session.commit()
                return seq

        same_build_task = None
        different_build_task = None
        async with session_factory() as blocker:
            await configure_session_authorization(blocker, system)
            locked = await DBResourceGovernanceRepository(
                blocker
            ).get_build(build_a, for_update=True)
            assert locked is not None
            try:
                same_build_task = asyncio.create_task(append_once(build_a))
                different_build_task = asyncio.create_task(
                    append_once(build_b)
                )

                # Build B is independent and must not wait for build A's row
                # lock. Build A must still be waiting when B has committed.
                different_seq = await asyncio.wait_for(
                    different_build_task,
                    timeout=5,
                )
                assert different_seq == 1
                assert same_build_task.done() is False

                await blocker.commit()
                same_seq = await asyncio.wait_for(
                    same_build_task,
                    timeout=5,
                )
                assert same_seq == 1
            finally:
                if blocker.in_transaction():
                    await blocker.rollback()
                pending = [
                    task
                    for task in (same_build_task, different_build_task)
                    if task is not None and not task.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(ResourceBuildORM).where(
                    ResourceBuildORM.id.in_(build_ids)
                )
            )
            await cleanup.commit()
        await engine.dispose()
