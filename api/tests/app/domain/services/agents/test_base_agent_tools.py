#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.tool_names import normalize_allowed_tool_names


class _DummyTool:
    def __init__(self, names):
        self._names = names

    def get_tools(self):
        return [{"function": {"name": name}} for name in self._names]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._names


class _DummyLLM:
    model_name = "test-model"
    supports_multimodal = False


class _TestAgent(BaseAgent):
    name = "test"


def test_messages_for_llm_strips_internal_fields():
    messages = [
        {"role": "assistant", "content": "ok"},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "_function_name": "write_file",
            "function_name": "write_file",
            "content": '{"success": true}',
        },
    ]
    sanitized = BaseAgent._messages_for_llm(messages)
    assert sanitized[1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"success": true}',
    }


def _agent_config(**overrides):
    defaults = {
        "max_retries": 1,
        "max_iterations": 1,
        "tool_result_max_chars": 8000,
    }
    defaults.update(overrides)
    return type("Cfg", (), defaults)()


def test_get_available_tools_respects_normalized_skill_whitelist():
    agent = _TestAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[_DummyTool(["read_file", "write_file", "search_web"])],
        allowed_tool_names=normalize_allowed_tool_names(["file_read", "write_file"]),
    )
    available = agent._get_available_tools()
    names = [item["function"]["name"] for item in available]
    assert names == ["read_file", "write_file"]


def test_get_available_tools_empty_whitelist_allows_all():
    agent = _TestAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[_DummyTool(["read_file", "write_file"])],
        allowed_tool_names=[],
    )
    names = [item["function"]["name"] for item in agent._get_available_tools()]
    assert names == ["read_file", "write_file"]


def test_get_available_tools_mcp_wildcard():
    agent = _TestAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[_DummyTool(["read_file", "mcp_jina_read_url"])],
        allowed_tool_names=["mcp_*"],
    )
    names = [item["function"]["name"] for item in agent._get_available_tools()]
    assert names == ["mcp_jina_read_url"]


def test_resolve_tool_uses_index():
    tool_pack = _DummyTool(["read_file"])
    agent = _TestAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[tool_pack],
    )
    resolved = agent._resolve_tool("read_file")
    assert resolved is tool_pack


def test_truncate_tool_result():
    large = ToolResult(success=True, data="x" * 100)
    truncated = BaseAgent._truncate_tool_result(large, max_chars=50)
    assert "结果已截断" in (truncated.message or "")
    assert truncated.data is not None
    assert len(large.model_dump_json()) > 50
