#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.utils.sandbox_result import (
    exec_command_await,
    file_content,
    shell_output,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_shell_output_from_dict():
    result = ToolResult(success=True, data={"output": "line1\nline2\n", "status": "completed"})
    assert shell_output(result) == "line1\nline2\n"


def test_shell_output_from_string():
    result = ToolResult(success=True, data="plain text")
    assert shell_output(result) == "plain text"


def test_shell_output_empty():
    assert shell_output(ToolResult(success=True, data=None)) == ""
    assert shell_output(ToolResult(success=True, data={})) == ""


def test_file_content_from_dict():
    result = ToolResult(success=True, data={"filepath": "/a.py", "content": "code"})
    assert file_content(result) == "code"


def test_file_content_from_string():
    result = ToolResult(success=True, data="legacy body")
    assert file_content(result) == "legacy body"


@pytest.mark.anyio
async def test_exec_command_await_completed():
    sandbox = MagicMock(spec=Sandbox)
    sandbox.exec_command = AsyncMock(
        return_value=ToolResult(
            success=True,
            data={"status": "completed", "returncode": 0, "output": "ok\n"},
        ),
    )
    output = await exec_command_await(sandbox, "s1", "/tmp", "echo ok")
    assert output == "ok\n"
    sandbox.wait_process.assert_not_called()


@pytest.mark.anyio
async def test_exec_command_await_running_waits():
    sandbox = MagicMock(spec=Sandbox)
    sandbox.exec_command = AsyncMock(
        return_value=ToolResult(success=True, data={"status": "running", "command": "git clone"}),
    )
    sandbox.wait_process = AsyncMock(
        return_value=ToolResult(success=True, data={"returncode": 0}),
    )
    sandbox.read_shell_output = AsyncMock(
        return_value=ToolResult(success=True, data={"output": "cloned\n"}),
    )
    output = await exec_command_await(sandbox, "s1", "/tmp", "git clone", timeout=300)
    assert output == "cloned\n"
    sandbox.wait_process.assert_awaited_once_with("s1", seconds=300)


@pytest.mark.anyio
async def test_exec_command_await_nonzero_exit_raises():
    sandbox = MagicMock(spec=Sandbox)
    sandbox.exec_command = AsyncMock(
        return_value=ToolResult(
            success=True,
            data={"status": "completed", "returncode": 1, "output": "fatal: repo not found"},
        ),
    )
    with pytest.raises(RuntimeError, match="exit=1"):
        await exec_command_await(sandbox, "s1", "/tmp", "git clone bad")
