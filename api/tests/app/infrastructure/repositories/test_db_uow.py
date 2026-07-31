#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("fk violation"))


async def _run_db_uow_reraises_commit_integrity_error():
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=_integrity_error())
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    uow = DBUnitOfWork(MagicMock())
    uow.db_session = mock_session

    with pytest.raises(IntegrityError):
        await uow.__aexit__(None, None, None)

    mock_session.rollback.assert_awaited()
    mock_session.close.assert_awaited()


def test_db_uow_reraises_commit_integrity_error():
    asyncio.run(_run_db_uow_reraises_commit_integrity_error())


async def _run_db_uow_preserves_body_exception_over_commit_error():
    mock_session = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    uow = DBUnitOfWork(MagicMock())
    uow.db_session = mock_session

    await uow.__aexit__(ValueError, ValueError("body failed"), None)

    mock_session.rollback.assert_awaited()
    mock_session.commit.assert_not_called()


def test_db_uow_preserves_body_exception_over_commit_error():
    asyncio.run(_run_db_uow_preserves_body_exception_over_commit_error())


async def _run_db_uow_propagates_commit_cancellation_after_cleanup():
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=asyncio.CancelledError())
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    uow = DBUnitOfWork(MagicMock())
    uow.db_session = mock_session

    with pytest.raises(asyncio.CancelledError):
        await uow.__aexit__(None, None, None)

    mock_session.rollback.assert_awaited()
    mock_session.close.assert_awaited()


def test_db_uow_propagates_commit_cancellation_after_cleanup():
    asyncio.run(_run_db_uow_propagates_commit_cancellation_after_cleanup())


async def _run_db_uow_sets_transaction_local_authorization_context():
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()
    principal = Principal(user_id="user-1", team_roles={"team-1": TeamRole.MEMBER})
    context = AuthorizationContext.for_principal(
        principal,
        scope=OwnerScope.team("user-1", "team-1"),
        request_id="request-1",
    )
    uow = DBUnitOfWork(lambda: mock_session, authorization_context=context)

    await uow.__aenter__()

    statement, params = mock_session.execute.await_args.args
    sql = str(statement)
    assert "set_config('app.auth_mode'" in sql
    assert "set_config('app.user_id'" in sql
    assert "set_config('app.team_id'" in sql
    assert "set_config('app.is_admin'" in sql
    assert "set_config('app.request_id'" in sql
    assert params == {
        "auth_mode": "user",
        "user_id": "user-1",
        "team_id": "team-1",
        "is_admin": "false",
        "request_id": "request-1",
        "system_actor": "",
    }

    await uow.commit()
    assert mock_session.execute.await_count == 2

    await uow.__aexit__(ValueError, ValueError("stop"), None)


def test_db_uow_sets_transaction_local_authorization_context():
    asyncio.run(_run_db_uow_sets_transaction_local_authorization_context())
