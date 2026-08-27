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
    }

    await uow.commit()
    assert session.execute.await_count == 1
    assert uow.state is UnitOfWorkState.COMMITTED

    await uow.__aexit__(None, None, None)
    session.rollback.assert_not_awaited()
