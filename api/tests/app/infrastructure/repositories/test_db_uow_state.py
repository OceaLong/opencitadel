from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.uow import UnitOfWorkState, UnitOfWorkStateError
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def _cipher() -> ApiKeyCipher:
    return ApiKeyCipher("uow-state-test-secret")


def _session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock()
    return session


def _uow(session: AsyncMock) -> DBUnitOfWork:
    return DBUnitOfWork(
        lambda: session,
        secret_cipher=_cipher(),
        audit_signing_key="uow-state-audit-signing-key",
        audit_signing_key_id="test",
        database_authorization_signing_secret="uow-state-authorization-secret",
        authorization_context=AuthorizationContext.system("uow-state-test"),
        cleanup_timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_successful_uncommitted_exit_rolls_back() -> None:
    session = _session()
    uow = _uow(session)

    async with uow:
        assert uow.state is UnitOfWorkState.ACTIVE

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()
    assert uow.state is UnitOfWorkState.CLOSED


@pytest.mark.asyncio
async def test_explicit_commit_happens_exactly_once() -> None:
    session = _session()
    uow = _uow(session)

    async with uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkStateError, match="committed"):
            await uow.commit()

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_rollback_is_idempotent_until_close() -> None:
    session = _session()
    uow = _uow(session)

    async with uow:
        await uow.rollback()
        await uow.rollback()

    session.rollback.assert_awaited_once()
    with pytest.raises(UnitOfWorkStateError, match="closed"):
        await uow.rollback()


@pytest.mark.asyncio
async def test_exception_exit_rolls_back_and_preserves_body_error() -> None:
    session = _session()
    uow = _uow(session)

    with pytest.raises(ValueError, match="body-failed"):
        async with uow:
            raise ValueError("body-failed")

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_enter_twice_and_use_after_close_are_rejected() -> None:
    session = _session()
    uow = _uow(session)

    await uow.__aenter__()
    with pytest.raises(UnitOfWorkStateError, match="active"):
        await uow.__aenter__()
    await uow.__aexit__(None, None, None)

    with pytest.raises(UnitOfWorkStateError, match="closed"):
        await uow.commit()


@pytest.mark.asyncio
async def test_cancellation_waits_for_rollback_before_closing_session() -> None:
    session = _session()
    rollback_started = asyncio.Event()
    release_rollback = asyncio.Event()
    events: list[str] = []

    async def rollback() -> None:
        events.append("rollback:start")
        rollback_started.set()
        await release_rollback.wait()
        events.append("rollback:end")

    async def close() -> None:
        events.append("close")

    session.rollback = AsyncMock(side_effect=rollback)
    session.close = AsyncMock(side_effect=close)
    uow = _uow(session)
    await uow.__aenter__()

    exiting = asyncio.create_task(uow.__aexit__(None, None, None))
    await asyncio.wait_for(rollback_started.wait(), 1)
    exiting.cancel()
    await asyncio.sleep(0)
    assert events == ["rollback:start"]

    release_rollback.set()
    with pytest.raises(asyncio.CancelledError):
        await exiting

    assert events == ["rollback:start", "rollback:end", "close"]
    assert uow.state is UnitOfWorkState.CLOSED


@pytest.mark.asyncio
async def test_factory_injects_shared_dependencies_and_fresh_authorization() -> None:
    sessions = [_session(), _session()]
    factory = create_uow_factory(
        session_factory=lambda: sessions.pop(0),
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=_cipher(),
            audit_signing_key="uow-state-audit-signing-key",
            audit_signing_key_id="test",
            database_authorization_signing_secret="uow-state-authorization-secret",
        ),
    )

    first = factory(AuthorizationContext.system("first"))
    second = factory(AuthorizationContext.system("second"))

    assert first is not second
    assert first.authorization_context.system_actor == "first"
    assert second.authorization_context.system_actor == "second"
