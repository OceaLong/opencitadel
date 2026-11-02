"""Effect acceptance keeps budget-first locking and fences elapsed authority."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.infrastructure.execution import postgres_effect_store as module
from app.infrastructure.execution.postgres_activity_fence import ActivityLeaseExpiredError


def session_fixture(now):
    transaction = object()
    session = SimpleNamespace(
        info={},
        sync_session=SimpleNamespace(get_transaction=lambda: transaction),
        scalar=AsyncMock(return_value=now),
        flush=AsyncMock(),
    )
    command = SimpleNamespace(command_id=uuid4(), command_type="ResolveActivityOutcome")
    return session, command


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_effect_permit_is_checked_against_database_clock_after_writes(expired):
    now = datetime.now(UTC)
    session, command = session_fixture(now)
    module._remember_effect_fence(session, command, now + timedelta(seconds=1))
    if expired:
        session.scalar.return_value = now + timedelta(seconds=2)
        with pytest.raises(ActivityLeaseExpiredError, match="before commit"):
            await module.ensure_effect_command_current(session, command)
    else:
        await module.ensure_effect_command_current(session, command)
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_effect_permit_cannot_cross_transactions_but_completed_replay_needs_no_new_permit():
    session, command = session_fixture(datetime.now(UTC))
    module._remember_effect_fence(session, command, datetime.now(UTC) + timedelta(seconds=10))
    session.sync_session.get_transaction = lambda: object()
    with pytest.raises(ActivityLeaseExpiredError):
        await module.ensure_effect_command_current(session, command)
    await module.ensure_effect_command_current(session, command, if_issued=True)


@pytest.mark.asyncio
async def test_preflight_cannot_take_scope_or_identity_locks_before_budget(monkeypatch):
    calls = []
    session, command = session_fixture(datetime.now(UTC))
    command.owner_user_id, command.team_id = "owner", None
    prepared = object()

    async def prepare(command, db):
        calls.append("budget")
        return prepared

    async def scope(*args):
        assert calls == ["budget"]
        calls.append("scope")

    async def preflight(uow):
        assert calls == ["budget", "scope"]
        calls.append("authority")

    async def handle(command, db, *, prepared):
        assert prepared is token
        assert calls == ["budget", "scope", "authority"]
        calls.append("facts")
        module._remember_effect_fence(db, command, datetime.now(UTC) + timedelta(seconds=10))
        return SimpleNamespace(status="accepted")

    token = prepared
    uow = SimpleNamespace(db_session=session, commit=AsyncMock())

    @asynccontextmanager
    async def factory():
        yield uow

    monkeypatch.setattr(module, "lock_scope", scope)
    store = module.PostgresEffectStore(
        session_factory=None,
        uow_factory=factory,
        orchestrator=SimpleNamespace(prepare_command_transaction=prepare, handle_in_session=handle),
    )
    result = await store.worker_command(command, preflight=preflight)
    assert result.status == "accepted"
    assert calls == ["budget", "scope", "authority", "facts"]
    uow.commit.assert_awaited_once()
