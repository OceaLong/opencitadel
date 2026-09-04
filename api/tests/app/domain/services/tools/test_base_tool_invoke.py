import pytest

from app.domain.models.tool_result import ToolResult, normalize_tool_result
from app.domain.services.tools.base import BaseTool, tool


class _StringTool(BaseTool):
    name: str = "string_tool"

    @tool(
        name="echo",
        description="Return a plain string",
        parameters={"text": {"type": "string", "description": "Text to echo"}},
        required=["text"],
    )
    async def echo(self, text: str) -> str:
        return text


class _StructuredTool(BaseTool):
    name: str = "structured_tool"

    @tool(
        name="run",
        description="Return a ToolResult",
        parameters={},
        required=[],
    )
    async def run(self) -> ToolResult:
        return ToolResult(success=False, message="failed", data=None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_normalize_tool_result_wraps_string():
    result = normalize_tool_result("hello")
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data == "hello"


def test_normalize_tool_result_passthrough():
    original = ToolResult(success=False, message="err", data={"k": 1})
    assert normalize_tool_result(original) is original


@pytest.mark.anyio
async def test_invoke_wraps_string_return():
    tool = _StringTool()
    result = await tool.invoke("echo", text="tree")
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data == "tree"


@pytest.mark.anyio
async def test_invoke_passthrough_tool_result():
    tool = _StructuredTool()
    result = await tool.invoke("run")
    assert result.success is False
    assert result.message == "failed"


@pytest.mark.anyio
async def test_shell_tool_on_cancel_kills_inflight_sessions():
    from unittest.mock import AsyncMock

    from app.domain.models.tool_result import ToolResult as _TR
    from app.domain.services.tools.shell import ShellTool

    sandbox = AsyncMock()
    sandbox.exec_command.return_value = _TR(success=True, data="ok")
    tool = ShellTool(sandbox=sandbox)

    # 正常完成：会话被移出在途集合，on_cancel 不再 kill。
    await tool.shell_execute("sess-1", "/work", "echo hi")
    await tool.on_cancel()
    sandbox.kill_process.assert_not_awaited()

    # 被取消：exec_command 抛 CancelledError，会话留在在途集合。
    import asyncio

    sandbox.exec_command.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await tool.shell_execute("sess-2", "/work", "sleep 100")
    await tool.on_cancel()
    sandbox.kill_process.assert_awaited_once_with("sess-2")


@pytest.mark.anyio
async def test_browser_tool_on_cancel_closes_page_best_effort():
    from unittest.mock import AsyncMock

    from app.domain.services.tools.browser import BrowserTool

    browser = AsyncMock()
    tool = BrowserTool(browser=browser)

    await tool.on_cancel()

    browser.cleanup.assert_awaited_once()


@pytest.mark.anyio
async def test_base_tool_on_cancel_defaults_to_noop():
    tool = _StringTool()

    assert await tool.on_cancel() is None
