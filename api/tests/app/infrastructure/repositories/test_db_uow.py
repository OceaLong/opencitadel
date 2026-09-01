import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole
from app.domain.repositories.uow import UnitOfWorkState
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("fk violation"))


def _make_uow(
    session: AsyncMock,
    *,
    authorization_context: AuthorizationContext | None = None,
) -> DBUnitOfWork:
    return DBUnitOfWork(
        lambda: session,
        secret_cipher=ApiKeyCipher("db-uow-test-secret"),
        audit_signing_key="db-uow-audit-signing-key",
        audit_signing_key_id="test",
        database_authorization_signing_secret="db-uow-authorization-secret",
        authorization_context=authorization_context,
    )


@pytest.mark.asyncio
async def test_db_uow_reraises_explicit_commit_integrity_error() -> None:
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=_integrity_error())
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    with pytest.raises(IntegrityError):
        async with _make_uow(session) as uow:
            await uow.commit()

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_db_uow_preserves_body_exception_over_cleanup() -> None:
    session = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    with pytest.raises(ValueError, match="body failed"):
        async with _make_uow(session):
            raise ValueError("body failed")

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_uow_propagates_commit_cancellation_after_cleanup() -> None:
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=asyncio.CancelledError())
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        async with _make_uow(session) as uow:
            await uow.commit()

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_db_uow_sets_transaction_local_authorization_context_once() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    principal = Principal(user_id="user-1", team_roles={"team-1": TeamRole.MEMBER})
    context = AuthorizationContext.for_principal(
        principal,
        scope=OwnerScope.team("user-1", "team-1"),
        request_id="request-1",
    )
    uow = _make_uow(session, authorization_context=context)

    await uow.__aenter__()

    assert uow.execution_commands._session is session
    statement, params = session.execute.await_args.args
    sql = str(statement)
    assert "set_config('app.auth_mode'" in sql
    assert "set_config('app.user_id'" in sql
    assert "set_config('app.team_id'" in sql
    assert "set_config('app.is_admin'" in sql
    assert "set_config('app.request_id'" in sql
    assert "set_config('app.auth_signature'" in sql
    assert len(params["auth_signature"]) == 64
    assert {key: value for key, value in params.items() if key != "auth_signature"} == {
        "auth_mode": "user",
        "user_id": "user-1",
        "team_id": "team-1",
        "is_admin": "false",
        "request_id": "request-1",
        "system_actor": "",
        "is_auditor": "false",
    }

    await uow.commit()
    assert session.execute.await_count == 1
    assert uow.state is UnitOfWorkState.COMMITTED

    await uow.__aexit__(None, None, None)
    session.rollback.assert_not_awaited()


def _make_read_only_uow(session: AsyncMock) -> DBUnitOfWork:
    return DBUnitOfWork(
        lambda: session,
        secret_cipher=ApiKeyCipher("db-uow-test-secret"),
        audit_signing_key="db-uow-audit-signing-key",
        audit_signing_key_id="test",
        database_authorization_signing_secret="db-uow-authorization-secret",
        read_only=True,
    )


@pytest.mark.asyncio
async def test_nested_write_uow_in_same_task_raises() -> None:
    from app.infrastructure.repositories.db_uow import NestedUnitOfWorkError

    session = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    with pytest.raises(NestedUnitOfWorkError, match="already active"):  # noqa: PT012 - nested UoW entry is the subject
        async with _make_uow(session):
            # A second write UoW opened inside the first (same task) would check
            # out a second pooled connection while the first is held -> guard.
            async with _make_uow(session):
                pass

    # The guard is released after the outer UoW unwinds, so a later top-level
    # write UoW enters cleanly.
    async with _make_uow(session):
        pass


@pytest.mark.asyncio
async def test_sequential_write_uows_in_same_task_do_not_trip_guard() -> None:
    session = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    async with _make_uow(session):
        pass
    async with _make_uow(session):
        pass


@pytest.mark.asyncio
async def test_read_only_uow_may_nest_inside_write_uow() -> None:
    session = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    # read_only UoWs are exempt (pending the broader P1-8 ReadOnlyUnitOfWork
    # rollout) so query-side reads nested under a write do not trip the guard.
    async with _make_uow(session), _make_read_only_uow(session):
        pass


@pytest.mark.asyncio
async def test_write_uows_in_independent_tasks_do_not_trip_guard() -> None:
    session = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    entered_first = asyncio.Event()
    entered_second = asyncio.Event()
    release = asyncio.Event()

    async def hold(entered: asyncio.Event) -> None:
        async with _make_uow(session):
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold(entered_first))
    second = asyncio.create_task(hold(entered_second))
    await asyncio.wait_for(entered_first.wait(), 1)
    await asyncio.wait_for(entered_second.wait(), 1)
    release.set()
    await asyncio.gather(first, second)
