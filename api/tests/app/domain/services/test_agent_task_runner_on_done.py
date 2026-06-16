#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.domain.models.session import SessionStatus
from app.domain.services.agent_task_runner import AgentTaskRunner


async def _run_on_done_skips_callback_when_not_completed():
    runner = object.__new__(AgentTaskRunner)
    runner._session_id = "sess-1"
    runner._on_complete_callback = AsyncMock()
    runner._terminal_session_status = SessionStatus.FAILED

    await AgentTaskRunner.on_done(runner, MagicMock())

    runner._on_complete_callback.assert_not_called()


def test_on_done_skips_callback_when_not_completed():
    asyncio.run(_run_on_done_skips_callback_when_not_completed())


async def _run_on_done_runs_callback_when_completed():
    runner = object.__new__(AgentTaskRunner)
    runner._session_id = "sess-1"
    runner._on_complete_callback = AsyncMock()
    runner._terminal_session_status = SessionStatus.COMPLETED

    await AgentTaskRunner.on_done(runner, MagicMock())

    runner._on_complete_callback.assert_awaited_once_with("sess-1")


def test_on_done_runs_callback_when_completed():
    asyncio.run(_run_on_done_runs_callback_when_completed())
