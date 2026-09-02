"""Projection rebuild must reproduce inline views from the verified journal."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.kernel.application.command_service import CommandService
from app.kernel.application.ports import KernelAuthorization
from app.kernel.application.projection_rebuilder import ProjectionRebuilder
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.domain.workflows.agent import agent_reducer
from app.kernel.infrastructure.postgres.models import (
    KERNEL_PROJECTION_TABLES,
    KERNEL_TABLES,
    KernelRunViewORM,
)
from app.kernel.infrastructure.postgres.rebuild import PostgresProjectionRebuildStore
from app.kernel.infrastructure.postgres.store import PostgresKernelStore

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def projection_factory():
    uri = os.getenv("KERNEL_V2_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("KERNEL_V2_TEST_DATABASE_URI is required for projection rebuild proofs")
    engine = create_async_engine(uri)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                + " RESTART IDENTITY CASCADE"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE "
                    + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                    + " RESTART IDENTITY CASCADE"
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_rebuild_matches_inline_projection(projection_factory) -> None:
    """Changing projector scheduling cannot change any Run query result."""

    run_id = UUID(int=5100)
    command = CommandEnvelope(
        command_id=UUID(int=5101),
        run_id=run_id,
        workflow=Workflow.AGENT,
        type="StartAgent",
        payload={"title": "Rebuild me", "prompt": "hello", "tool_catalog": []},
        expected_stream_version=0,
        owner_scope=OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="request-rebuild",
        submitted_at=NOW,
    )

    def facts(envelope, state):
        return DecisionFacts(
            now=NOW,
            actor_user_id="user-1",
            request_id="request-rebuild",
            policy_revision_id=UUID(int=5102),
            event_ids=(UUID(int=5103), UUID(int=5104), UUID(int=5105)),
            effect_ids=(UUID(int=5106),),
        )

    store = PostgresKernelStore(
        projection_factory,
        encrypt_private=lambda value: json.dumps(value, sort_keys=True),
        decrypt_private=json.loads,
    )
    service = CommandService(
        store=store,
        reducers=ReducerRegistry({Workflow.AGENT: agent_reducer}),
        facts_factory=facts,
    )
    await service.submit(
        command,
        KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1")),
    )

    async with projection_factory() as session:
        inline = await session.get(KernelRunViewORM, run_id)
        assert inline is not None
        expected = (inline.title, inline.status, inline.current_turn, inline.stream_version)
        for table in reversed(KERNEL_PROJECTION_TABLES):
            await session.execute(delete(table))
        await session.commit()

    await ProjectionRebuilder(
        PostgresProjectionRebuildStore(
            projection_factory,
            decrypt_private=json.loads,
        )
    ).rebuild()

    async with projection_factory() as session:
        rebuilt = await session.scalar(
            select(KernelRunViewORM).where(KernelRunViewORM.id == run_id)
        )

    assert rebuilt is not None
    assert (rebuilt.title, rebuilt.status, rebuilt.current_turn, rebuilt.stream_version) == expected
