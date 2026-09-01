"""Session deletion cannot orphan a live execution Run."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.session_service import SessionService
from app.domain.errors import BadRequestError
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session


def _uow(session: Session):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.soft_delete = AsyncMock(return_value=True)
    uow.session.delete_by_id = AsyncMock()
    return uow


@pytest.mark.asyncio
async def test_delete_session_soft_deletes_and_destroys_sandbox_after_run_is_terminal():
    """删除改为软删（进入回收站），仍在删除时销毁可重建的 sandbox。"""
    session = Session(
        id="sess-1",
        sandbox_id="sandbox-1",
        owner_user_id="user-1",
    )
    uow = _uow(session)
    sandbox = MagicMock()
    sandbox.destroy = AsyncMock()
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=sandbox)
    run_projection = MagicMock()
    run_projection.latest_active_run_id = AsyncMock(return_value=None)
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=sandbox_cls,
        run_projection=run_projection,
        session_list_publisher=AsyncMock(),
    )

    scope = OwnerScope.personal("user-1")
    await service.delete_session("sess-1", scope=scope)

    sandbox.destroy.assert_awaited_once()
    # 软删：设置 deleted_at，不物理删除
    uow.session.soft_delete.assert_awaited_once_with("sess-1", scope=scope)
    uow.session.delete_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_session_rejects_an_active_run():
    session = Session(id="sess-1", owner_user_id="user-1")
    uow = _uow(session)
    run_projection = MagicMock()
    run_projection.latest_active_run_id = AsyncMock(
        return_value="72d3834b-e946-48f0-85bc-831bf7b3a755"
    )
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        run_projection=run_projection,
        session_list_publisher=AsyncMock(),
    )

    with pytest.raises(BadRequestError, match="活动 Run"):
        await service.delete_session(
            "sess-1",
            scope=OwnerScope.personal("user-1"),
        )

    uow.session.soft_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_session_clears_deleted_at():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.restore = AsyncMock(return_value=True)
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        run_projection=MagicMock(),
        session_list_publisher=AsyncMock(),
    )

    scope = OwnerScope.personal("user-1")
    await service.restore_session("sess-1", scope=scope)

    uow.session.restore.assert_awaited_once_with("sess-1", scope=scope)
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_session_physically_deletes_recycle_bin_row():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.purge = AsyncMock(return_value=True)
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        run_projection=MagicMock(),
        session_list_publisher=AsyncMock(),
    )

    scope = OwnerScope.personal("user-1")
    await service.purge_session("sess-1", scope=scope)

    uow.session.purge.assert_awaited_once_with("sess-1", scope=scope)
    uow.commit.assert_awaited_once()
