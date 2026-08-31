import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.session_service import SessionService
from app.domain.errors import NotFoundError
from app.domain.models.codebase import SessionMode


async def _create_kb_agent_session():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.save = AsyncMock()
    uow.knowledge_base.get_kb = AsyncMock(return_value=MagicMock())

    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        run_projection=AsyncMock(),
        session_list_publisher=AsyncMock(),
    )
    session = await service.create_session(
        knowledge_base_id="kb-1",
        mode=SessionMode.AGENT,
    )

    return session, uow


def test_create_kb_agent_session_preserves_agent_mode():
    """Catches creation silently downgrading a selected KB Agent session to Ask."""
    session, uow = asyncio.run(_create_kb_agent_session())

    assert session.mode == SessionMode.AGENT
    assert uow.session.save.await_args.args[0].mode == SessionMode.AGENT


def test_missing_session_files_are_a_controlled_not_found() -> None:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_files = AsyncMock(return_value=None)
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        run_projection=AsyncMock(),
        session_list_publisher=AsyncMock(),
    )

    with pytest.raises(NotFoundError, match="会话不存在"):
        asyncio.run(service.get_session_files("missing-session"))
