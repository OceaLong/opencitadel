#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import MagicMock

import pytest

from app.domain.services.agent_task_runner import AgentTaskRunner, FILE_MUTATING_FUNCTIONS


@pytest.mark.parametrize(
    ("function_name", "should_sync"),
    [
        ("write_file", True),
        ("replace_in_file", True),
        ("read_file", False),
        ("search_in_file", False),
        ("find_files", False),
    ],
)
def test_file_mutating_functions(function_name, should_sync):
    assert (function_name in FILE_MUTATING_FUNCTIONS) is should_sync


async def _run_build_vision_attachments_empty_returns_without_flow_react():
    runner = object.__new__(AgentTaskRunner)
    runner._llm = MagicMock()

    result = await AgentTaskRunner._build_vision_attachments(runner, [])

    assert result == []


def test_build_vision_attachments_empty_returns_without_flow_react():
    asyncio.run(_run_build_vision_attachments_empty_returns_without_flow_react())
