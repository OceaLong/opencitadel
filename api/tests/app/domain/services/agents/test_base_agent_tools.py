#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.tool_names import normalize_allowed_tool_names


class _DummyTool:
    def __init__(self, names):
        self._names = names

    def get_tools(self):
        return [{"function": {"name": name}} for name in self._names]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._names


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


def test_get_available_tools_respects_normalized_skill_whitelist():
    agent = _TestAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=type("Cfg", (), {"max_retries": 1, "max_iterations": 1})(),
        llm=object(),
        json_parser=object(),
        tools=[_DummyTool(["read_file", "write_file", "search_web"])],
        allowed_tool_names=normalize_allowed_tool_names(["file_read", "write_file"]),
    )
    available = agent._get_available_tools()
    names = [item["function"]["name"] for item in available]
    assert names == ["read_file", "write_file"]
