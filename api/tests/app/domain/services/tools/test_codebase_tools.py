#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.codebase_tools import CodebaseTool


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_read_code_joins_workspace_path():
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(
        return_value=ToolResult(success=True, data={"content": "print('hi')"}),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        workspace_path="/home/ubuntu/codebase",
    )

    result = await tool.read_code("src/main.py")

    sandbox.read_file.assert_awaited_once_with(
        "/home/ubuntu/codebase/src/main.py",
        start_line=None,
        end_line=None,
    )
    assert "src/main.py" in result
    assert "print('hi')" in result


@pytest.mark.anyio
async def test_read_code_returns_error_message_on_failure():
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(
        return_value=ToolResult(success=False, message="not found"),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        workspace_path="/home/ubuntu/codebase",
    )

    result = await tool.read_code("missing.py")

    assert "读取失败" in result
    assert "not found" in result
