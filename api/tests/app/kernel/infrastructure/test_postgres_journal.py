"""Real PostgreSQL proofs for atomic command, journal, Effect, and projection writes."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.contexts.identity.models import UserORM, UserQuotaORM
from app.contexts.inference.quota import PostgresQuotaGate
from app.kernel.application.command_service import CommandService
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.domain.workflows.agent import agent_reducer
from app.kernel.infrastructure.postgres.models import (
    KERNEL_TABLES,
    KernelCommandORM,
    KernelEffectORM,
    KernelEventORM,
    KernelRunORM,
    KernelRunViewORM,
)
from app.kernel.infrastructure.postgres.projections import ProjectionRegistry
from app.kernel.infrastructure.postgres.store import PostgresKernelStore

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID(int=4100)
COMMAND_ID = UUID(int=4101)


def _cipher(data: dict[str, object]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _decipher(value: str) -> dict[str, object]:
    return json.loads(value)


@pytest_asyncio.fixture
async def pg_factory():
    uri = os.getenv("KERNEL_V2_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("KERNEL_V2_TEST_DATABASE_URI is required for kernel PostgreSQL proofs")
    engine = create_async_engine(uri)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                + " RESTART IDENTITY CASCADE"
            )
        )
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={"database_authorization_signing_secret": "kernel-v2-test-signing-secret"},
    )
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


def _command(
    *,
    command_id: UUID = COMMAND_ID,
    run_id: UUID = RUN_ID,
    prompt: str = "inspect",
    actor_user_id: str = "user-1",
):
    return CommandEnvelope(
        command_id=command_id,
        run_id=run_id,
        workflow=Workflow.AGENT,
        type="StartAgent",
        payload={"title": "Audit", "prompt": prompt, "tool_catalog": []},
        expected_stream_version=0,
        owner_scope=OwnerScopeRef.personal(actor_user_id),
        actor_user_id=actor_user_id,
        request_id=f"request-{command_id}",
        submitted_at=NOW,
    )


def _facts(command, state):
    seed = command.command_id.int * 10
    return DecisionFacts(
        now=NOW,
        actor_user_id=command.actor_user_id,
        request_id=command.request_id,
        policy_revision_id=UUID(int=4200),
        event_ids=tuple(UUID(int=seed + index) for index in range(1, 8)),
        effect_ids=(UUID(int=seed + 8),),
    )


def _service(
    pg_factory,
    projectors: ProjectionRegistry | None = None,
    *,
    command_validator=None,
):
    store = PostgresKernelStore(
        pg_factory,
        encrypt_private=_cipher,
        decrypt_private=_decipher,
        projections=projectors,
        command_validator=command_validator,
    )
    return CommandService(
        store=store,
        reducers=ReducerRegistry({Workflow.AGENT: agent_reducer}),
        facts_factory=_facts,
    )


def _authorization():
    return KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1"))


@pytest.mark.asyncio
async def test_command_appends_events_effect_and_view_in_one_commit(pg_factory) -> None:
    """Omitting any transaction participant must leave this observable graph incomplete."""

    result = await _service(pg_factory).submit(_command(), _authorization())

    async with pg_factory() as session:
        counts = {
            "runs": await session.scalar(select(func.count()).select_from(KernelRunORM)),
            "commands": await session.scalar(select(func.count()).select_from(KernelCommandORM)),
            "events": await session.scalar(select(func.count()).select_from(KernelEventORM)),
            "effects": await session.scalar(select(func.count()).select_from(KernelEffectORM)),
            "views": await session.scalar(select(func.count()).select_from(KernelRunViewORM)),
        }
        view = await session.get(KernelRunViewORM, RUN_ID)
        command_row = await session.get(KernelCommandORM, COMMAND_ID)

    assert result.stream_version == 3
    assert counts == {"runs": 1, "commands": 1, "events": 3, "effects": 1, "views": 1}
    assert view is not None
    assert view.status == "running"
    assert view.title == "Audit"
    assert command_row is not None
    assert not hasattr(command_row, "payload")
    assert len(command_row.payload_digest) == 64


@pytest.mark.asyncio
async def test_concurrent_duplicate_command_reuses_one_persisted_result(pg_factory) -> None:
    """Two simultaneous client retries must still append one decision."""

    service = _service(pg_factory)
    first, second = await asyncio.gather(
        service.submit(_command(), _authorization()),
        service.submit(_command(), _authorization()),
    )

    async with pg_factory() as session:
        event_count = await session.scalar(select(func.count()).select_from(KernelEventORM))
        effect_count = await session.scalar(select(func.count()).select_from(KernelEffectORM))

    assert first == second
    assert event_count == 3
    assert effect_count == 1


@pytest.mark.asyncio
async def test_concurrent_run_admission_is_atomic_per_user(pg_factory) -> None:
    actor = "quota-user-4100"
    async with pg_factory() as session, session.begin():
        session.add(
            UserORM(
                id=actor,
                email=f"{actor}@example.test",
                username=actor,
                password_hash=None,
                display_name="Quota User",
                global_role="user",
                enabled=True,
                token_version=0,
                created_at=NOW,
                updated_at=NOW,
                last_login_at=None,
            )
        )
        session.add(
            UserQuotaORM(
                user_id=actor,
                monthly_model_tokens=None,
                daily_new_runs=1,
                concurrent_runs=1,
                storage_bytes=None,
                updated_at=NOW,
            )
        )
    quota = PostgresQuotaGate(pg_factory)
    service = _service(pg_factory, command_validator=quota.validate_command)
    commands = (
        _command(
            command_id=UUID(int=4401),
            run_id=UUID(int=4411),
            actor_user_id=actor,
        ),
        _command(
            command_id=UUID(int=4402),
            run_id=UUID(int=4412),
            actor_user_id=actor,
        ),
    )
    authorization = KernelAuthorization.for_user(
        actor,
        OwnerScopeRef.personal(actor),
    )
    try:
        results = await asyncio.gather(
            *(service.submit(command, authorization) for command in commands),
            return_exceptions=True,
        )
        async with pg_factory() as session:
            run_count = await session.scalar(select(func.count()).select_from(KernelRunORM))
            command_count = await session.scalar(select(func.count()).select_from(KernelCommandORM))
        assert sum(not isinstance(value, BaseException) for value in results) == 1
        assert sum(isinstance(value, BaseException) for value in results) == 1
        assert run_count == 1
        assert command_count == 1
    finally:
        async with pg_factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_quotas WHERE user_id = :user_id"),
                {"user_id": actor},
            )
            await session.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": actor},
            )


@pytest.mark.asyncio
async def test_projection_failure_rolls_back_command_journal_and_effect(pg_factory) -> None:
    """A projection exception must not expose a partially accepted command."""

    class BrokenProjection(ProjectionRegistry):
        async def apply(self, session, event, private_payload):
            raise RuntimeError("projection failed")

    with pytest.raises(RuntimeError, match="projection failed"):
        await _service(pg_factory, BrokenProjection()).submit(_command(), _authorization())

    async with pg_factory() as session:
        counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (KernelRunORM, KernelCommandORM, KernelEventORM, KernelEffectORM)
        ]

    assert counts == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_purge_physically_erases_run_journal_effects_and_private_commands(pg_factory) -> None:
    service = _service(pg_factory)
    await service.submit(_command(), _authorization())
    archive = CommandEnvelope(
        command_id=UUID(int=4301),
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        type="ArchiveRun",
        payload={"purge_after": NOW.isoformat()},
        expected_stream_version=None,
        owner_scope=OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="archive-request",
        submitted_at=NOW,
    )
    await service.submit(archive, _authorization())
    purge = archive.model_copy(
        update={
            "command_id": UUID(int=4302),
            "type": "PurgeRun",
            "payload": {"plan_hash": "a" * 64},
            "request_id": "purge-request",
        }
    )
    await service.submit(purge, _authorization())

    async with pg_factory() as session:
        counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (KernelRunORM, KernelEventORM, KernelEffectORM, KernelRunViewORM)
        ]
        commands = (await session.scalars(select(KernelCommandORM))).all()

    assert counts == [0, 0, 0, 0]
    assert [row.id for row in commands] == [purge.command_id]
    assert "inspect" not in commands[0].payload_ciphertext
