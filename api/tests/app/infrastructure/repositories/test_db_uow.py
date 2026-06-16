#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.infrastructure.repositories.db_uow import DBUnitOfWork


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
