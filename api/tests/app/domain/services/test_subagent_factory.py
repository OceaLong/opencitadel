#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from typing import AsyncGenerator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent
from app.domain.services.subagent_factory import (
    build_subagent_tool,
    extract_last_assistant_text,
    finalize_subagent_summary,
)


class _FakeMemory:
    def __init__(self, messages: List[dict]):
        self._messages = messages

    def get_messages(self) -> List[dict]:
        return list(self._messages)


class _FakeSubAgent:
    def __init__(self, events: List[BaseEvent], memory_messages: Optional[List[dict]] = None):
        self._events = events
        self._memory = _FakeMemory(memory_messages or [])
        self._llm = AsyncMock()
        self.name = "subagent"

    def set_locale(self, locale: str) -> None:
        pass

    async def _ensure_memory(self) -> None:
        pass

    async def invoke(self, *args, **kwargs) -> AsyncGenerator[BaseEvent, None]:
        for event in self._events:
            yield event


def test_extract_last_assistant_text_skips_empty_and_tool_only():
    agent = MagicMock()
    agent._memory = _FakeMemory([
        {"role": "user", "content": "goal"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "content": "  partial note  "},
    ])
    assert extract_last_assistant_text(agent) == "partial note"


async def _test_finalize_subagent_summary_invokes_llm_without_tools():
    agent = _FakeSubAgent([], memory_messages=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "goal"},
    ])
    agent._llm.invoke = AsyncMock(return_value={"content": "Final summary text"})

    summary = await finalize_subagent_summary(agent, "research distances", "en")
    assert summary == "Final summary text"
    agent._llm.invoke.assert_awaited_once()
    call_kwargs = agent._llm.invoke.await_args.kwargs
    assert call_kwargs.get("tools") == []


def test_finalize_subagent_summary_invokes_llm_without_tools():
    asyncio.run(_test_finalize_subagent_summary_invokes_llm_without_tools())


async def _test_run_subagent_propagates_error_event_message():
    fake_agent = _FakeSubAgent([
        ErrorEvent(error="Agent迭代超过最大迭代次数: 15, 任务处理失败"),
        ErrorEvent(error="Agent未能生成有效回复内容"),
    ])

    with patch("app.domain.services.subagent_factory.SubAgentAgent", return_value=fake_agent), patch(
            "app.domain.services.subagent_factory.ToolRegistry.build_default_tools", return_value=[]
    ), patch("app.domain.services.subagent_factory.compose_system_prompt", return_value="sys"), patch(
            "app.domain.services.subagent_factory.finalize_subagent_summary",
            AsyncMock(return_value=""),
    ):
        tool = build_subagent_tool(
            uow_factory=MagicMock(),
            session_id="sess-1",
            llm=AsyncMock(),
            agent_config=AgentConfig(),
            json_parser=AsyncMock(),
            browser=MagicMock(),
            sandbox=MagicMock(),
            search_engine=MagicMock(),
            mcp_tool=MagicMock(),
            a2a_tool=MagicMock(),
            observability_port=MagicMock(),
            runtime_settings=MagicMock(),
            prompt_locale="zh",
        )
        result = await tool.delegate_subtask("调研路线")

    assert result.success is False
    assert "Agent迭代超过最大迭代次数" in result.message


def test_run_subagent_propagates_error_event_message():
    asyncio.run(_test_run_subagent_propagates_error_event_message())


async def _test_run_subagent_recovers_from_memory_when_no_message_event():
    fake_agent = _FakeSubAgent(
        [ErrorEvent(error="Agent未能生成有效回复内容")],
        memory_messages=[
            {"role": "assistant", "content": "成都到青城山约 60 公里。"},
        ],
    )

    with patch("app.domain.services.subagent_factory.SubAgentAgent", return_value=fake_agent), patch(
            "app.domain.services.subagent_factory.ToolRegistry.build_default_tools", return_value=[]
    ), patch("app.domain.services.subagent_factory.compose_system_prompt", return_value="sys"):
        tool = build_subagent_tool(
            uow_factory=MagicMock(),
            session_id="sess-1",
            llm=AsyncMock(),
            agent_config=AgentConfig(),
            json_parser=AsyncMock(),
            browser=MagicMock(),
            sandbox=MagicMock(),
            search_engine=MagicMock(),
            mcp_tool=MagicMock(),
            a2a_tool=MagicMock(),
            observability_port=MagicMock(),
            runtime_settings=MagicMock(),
            prompt_locale="zh",
        )
        result = await tool.delegate_subtask("调研距离")

    assert result.success is True
    assert result.data["summary"] == "成都到青城山约 60 公里。"


def test_run_subagent_recovers_from_memory_when_no_message_event():
    asyncio.run(_test_run_subagent_recovers_from_memory_when_no_message_event())


async def _test_run_subagent_finalize_summary_when_invoke_empty():
    fake_agent = _FakeSubAgent([], memory_messages=[{"role": "user", "content": "goal"}])

    with patch("app.domain.services.subagent_factory.SubAgentAgent", return_value=fake_agent), patch(
            "app.domain.services.subagent_factory.ToolRegistry.build_default_tools", return_value=[]
    ), patch("app.domain.services.subagent_factory.compose_system_prompt", return_value="sys"), patch(
            "app.domain.services.subagent_factory.finalize_subagent_summary",
            AsyncMock(return_value="收尾摘要"),
    ) as mock_finalize:
        tool = build_subagent_tool(
            uow_factory=MagicMock(),
            session_id="sess-1",
            llm=AsyncMock(),
            agent_config=AgentConfig(),
            json_parser=AsyncMock(),
            browser=MagicMock(),
            sandbox=MagicMock(),
            search_engine=MagicMock(),
            mcp_tool=MagicMock(),
            a2a_tool=MagicMock(),
            observability_port=MagicMock(),
            runtime_settings=MagicMock(),
            prompt_locale="zh",
        )
        result = await tool.delegate_subtask("调研路线")

    assert result.success is True
    assert result.data["summary"] == "收尾摘要"
    mock_finalize.assert_awaited_once()


def test_run_subagent_finalize_summary_when_invoke_empty():
    asyncio.run(_test_run_subagent_finalize_summary_when_invoke_empty())
