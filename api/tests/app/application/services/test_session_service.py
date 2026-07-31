#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.application.services.session_service import SessionService
from app.domain.models.codebase import SessionMode


async def _create_kb_agent_session():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.save = AsyncMock()
    uow.knowledge_base.get_kb = AsyncMock(return_value=MagicMock())

    service = SessionService(uow_factory=lambda: uow, sandbox_cls=MagicMock())
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
