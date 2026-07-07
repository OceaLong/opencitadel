#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

import pytest

from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.codebase import SessionMode
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.domain.services.flows.doc_qa_flow import DocQAFlow
from app.domain.services.flows.hybrid_ask_flow import HybridAskFlow
from app.domain.services.flows.planner_react import PlannerReActFlow

_PATCH_TARGETS = (
    "app.domain.services.agent_task_runner.MCPTool",
    "app.domain.services.agent_task_runner.A2ATool",
    "app.domain.services.agent_task_runner.AgentEventEmitter",
    "app.domain.services.agent_task_runner.AgentAttachmentSyncer",
    "app.domain.services.agent_task_runner.SandboxLifecycleCoordinator",
    "app.domain.services.agent_task_runner.ToolEventPresenter",
)


def _build_runner(
        *,
        mode: SessionMode,
        codebase_id: str | None = None,
        knowledge_base_id: str | None = None,
) -> AgentTaskRunner:
    patches = [patch(target) for target in _PATCH_TARGETS]
    for item in patches:
        item.start()
    try:
        return AgentTaskRunner(
            uow_factory=MagicMock(),
            llm=MagicMock(),
            agent_config=AgentConfig(),
            session_id="sess-1",
            json_parser=MagicMock(),
            browser=MagicMock(),
            sandbox=MagicMock(),
            search_engine=MagicMock(),
            file_storage=MagicMock(),
            mcp_config=MCPConfig(),
            a2a_config=A2AConfig(),
            sandbox_provider=MagicMock(),
            task_state_port=MagicMock(),
            observability_port=MagicMock(),
            event_sequence_port=MagicMock(),
            session_state_port=MagicMock(),
            runtime_settings=AgentRuntimeSettings(),
            mcp_connection_pool=MagicMock(),
            a2a_connection_pool=MagicMock(),
            mode=mode,
            codebase_id=codebase_id,
            knowledge_base_id=knowledge_base_id,
        )
    finally:
        for item in patches:
            item.stop()


@pytest.mark.parametrize(
    ("codebase_id", "knowledge_base_id", "mode", "expected_flow"),
    [
        (None, "kb-1", SessionMode.ASK, DocQAFlow),
        (None, "kb-1", SessionMode.AGENT, DocQAFlow),
        ("cb-1", "kb-1", SessionMode.ASK, HybridAskFlow),
        ("cb-1", "kb-1", SessionMode.AGENT, PlannerReActFlow),
        ("cb-1", None, SessionMode.ASK, CodeAskFlow),
        ("cb-1", None, SessionMode.AGENT, PlannerReActFlow),
    ],
)
def test_flow_routing(codebase_id, knowledge_base_id, mode, expected_flow):
    runner = _build_runner(
        mode=mode,
        codebase_id=codebase_id,
        knowledge_base_id=knowledge_base_id,
    )
    assert isinstance(runner._flow, expected_flow)
