#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from typing import List

from app.domain.models.audit_log import AuditLog
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.base import BaseAgent
from tests.app.domain.services.agents.conftest import agent_test_observability_port, agent_test_runtime_settings


class _FailingTool:
    name = "browser"

    def get_tools(self):
        return [{"function": {"name": "browser_click"}}]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name == "browser_click"

    async def invoke(self, tool_name: str, **kwargs):
        raise RuntimeError("simulated failure")


class _FakeAuditRepo:
    def __init__(self):
        self.items: List[AuditLog] = []

    async def add(self, log: AuditLog) -> None:
        self.items.append(log)


class _FakeSessionRepo:
    async def get_by_id(self, session_id: str):
        return type("Session", (), {"owner_user_id": "u1", "team_id": None})()


class _FakeUow:
    def __init__(self, audit_repo: _FakeAuditRepo):
        self.audit = audit_repo
        self.session = _FakeSessionRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


class _DummyLLM:
    model_name = "test-model"
    supports_multimodal = False


class _TestAgent(BaseAgent):
    name = "test"


def _agent_config(**overrides):
    defaults = {
        "max_retries": 1,
        "max_iterations": 1,
        "tool_result_max_chars": 8000,
    }
    defaults.update(overrides)
    return type("Cfg", (), defaults)()


def _make_agent(audit_repo: _FakeAuditRepo, **kwargs):
    defaults = {
        "uow_factory": lambda: _FakeUow(audit_repo),
        "session_id": "session-op-1",
        "agent_config": _agent_config(),
        "llm": _DummyLLM(),
        "json_parser": object(),
        "tools": [_FailingTool()],
        "observability_port": agent_test_observability_port(),
        "runtime_settings": agent_test_runtime_settings(gate_profile="standard"),
    }
    defaults.update(kwargs)
    return _TestAgent(**defaults)


def test_should_audit_tool_only_for_operator_sessions():
    operator_agent = _TestAgent(
        uow_factory=lambda: _FakeUow(_FakeAuditRepo()),
        session_id="s1",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(gate_profile="standard"),
    )
    plain_agent = _TestAgent(
        uow_factory=lambda: _FakeUow(_FakeAuditRepo()),
        session_id="s2",
        agent_config=_agent_config(),
        llm=_DummyLLM(),
        json_parser=object(),
        tools=[],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    assert operator_agent._should_audit_tool("browser_click") is True
    assert plain_agent._should_audit_tool("browser_click") is False
    assert plain_agent._should_audit_tool("shell_execute") is False


def test_failed_tool_invoke_writes_audit_for_operator():
    audit_repo = _FakeAuditRepo()
    agent = _make_agent(audit_repo)
    tool = _FailingTool()

    result = asyncio.run(agent._invoke_tool(tool, "browser_click", {"selector": "#btn-save"}))

    assert result.success is False
    assert len(audit_repo.items) == 1
    entry = audit_repo.items[0]
    assert entry.action == "agent_tool_invoke"
    assert entry.resource_id == "session-op-1"
    assert entry.metadata["tool"] == "browser_click"
    assert entry.metadata["success"] is False
    assert entry.metadata["gate_profile"] == "standard"
    assert "gated" in entry.metadata


def test_failed_tool_invoke_skips_audit_without_gate_profile():
    audit_repo = _FakeAuditRepo()
    agent = _make_agent(
        audit_repo,
        runtime_settings=agent_test_runtime_settings(),
    )
    tool = _FailingTool()

    result = asyncio.run(agent._invoke_tool(tool, "browser_click", {"selector": "#btn-save"}))

    assert result.success is False
    assert audit_repo.items == []
