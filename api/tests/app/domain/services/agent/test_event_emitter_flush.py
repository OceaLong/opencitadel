#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.event import MessageEvent
from app.domain.services.agent.event_emitter import AgentEventEmitter


async def _test_flush_keeps_buffer_on_persist_failure():
    uow = MagicMock()
    uow.session.add_event_payloads = AsyncMock(side_effect=RuntimeError("db down"))
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)

    emitter = AgentEventEmitter(
        session_id="sess-1",
        uow_factory=lambda: uow,
        event_sequence=MagicMock(),
        task_state_port=MagicMock(),
    )
    event = MessageEvent(role="assistant", message="hello")
    emitter._persist_buffer.append((event, event.model_dump(mode="json")))

    with pytest.raises(RuntimeError):
        await emitter.flush()

    assert len(emitter._persist_buffer) == 1


async def _test_flush_clears_buffer_on_success():
    uow = MagicMock()
    uow.session.add_event_payloads = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)

    emitter = AgentEventEmitter(
        session_id="sess-1",
        uow_factory=lambda: uow,
        event_sequence=MagicMock(),
        task_state_port=MagicMock(),
    )
    event = MessageEvent(role="assistant", message="hello")
    emitter._persist_buffer.append((event, event.model_dump(mode="json")))

    await emitter.flush()

    assert emitter._persist_buffer == []


def test_flush_keeps_buffer_on_persist_failure():
    asyncio.run(_test_flush_keeps_buffer_on_persist_failure())


def test_flush_clears_buffer_on_success():
    asyncio.run(_test_flush_clears_buffer_on_success())
