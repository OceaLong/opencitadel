#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

from app.domain.models.event import MessageEvent, ToolEvent
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import READ_SAFE
from app.domain.services.tools.subagent import SubAgentOutcome, SubAgentTool
from app.interfaces.schemas.event import EventMapper
from tests.app.domain.services.agents.conftest import (
    agent_test_observability_port,
    agent_test_runtime_settings,
)


class _LLM:
    model_name = "test-model"
    supports_multimodal = False


class _CitationTool(BaseTool):
    name = "knowledge_base"

    @tool(
        name="kb_search",
        description="search",
        parameters={},
        required=[],
        policy=READ_SAFE,
    )
    async def kb_search(self):
        return ToolResult(
            data="trusted source",
            citations=[
                KnowledgeCitation(
                    version_id="kbv1",
                    document_revision_id="revision1",
                    doc_id="doc1",
                    page_no=1,
                    chunk_id="chunk1",
                )
            ],
        )


class _DelegatingSubAgentTool(BaseTool):
    name = "subagent"

    def __init__(self, delegate: SubAgentTool):
        super().__init__()
        self._delegate = delegate

    @tool(
        name="delegate_subtask",
        description="delegate",
        parameters={
            "goal": {"type": "string"},
        },
        required=["goal"],
        policy=READ_SAFE,
    )
    async def delegate_subtask(self, goal: str):
        return await self._delegate.delegate_subtask(goal)


class _Agent(BaseAgent):
    name = "citation-test"

    async def _invoke_llm(self, *args, **kwargs):
        self._last_llm_message = {"content": "final answer"}
        if False:
            yield


@pytest.mark.anyio
async def test_tool_result_citations_reach_tool_and_final_message_events():
    agent = _Agent(
        uow_factory=lambda: None,
        session_id="session1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=object(),
        tools=[_CitationTool()],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    events = [
        event
        async for event in agent._run_tool_iteration_loop(
            {
                "tool_calls": [
                    {
                        "id": "call1",
                        "function": {
                            "name": "kb_search",
                            "arguments": {},
                        },
                    }
                ]
            },
            None,
            emit_deltas=False,
            response_schema=None,
        )
    ]

    tool_event = next(item for item in events if isinstance(item, ToolEvent))
    final_event = next(
        item for item in events if isinstance(item, MessageEvent)
    )
    assert tool_event.citations
    assert final_event.citations == tool_event.citations


@pytest.mark.anyio
async def test_subagent_citations_reach_parent_sse_and_replay():
    citation = KnowledgeCitation(
        version_id="kbv1",
        document_revision_id="revision1",
        doc_id="doc1",
        page_no=1,
        chunk_id="chunk1",
    )

    async def _run_subagent(**kwargs):
        return SubAgentOutcome(
            summary="trusted subagent answer",
            citations=(citation,),
        )

    delegate = SubAgentTool(run_subagent=_run_subagent)
    agent = _Agent(
        uow_factory=lambda: None,
        session_id="session1",
        agent_config=SimpleNamespace(
            max_retries=1,
            max_iterations=1,
            tool_result_max_chars=8000,
        ),
        llm=_LLM(),
        json_parser=object(),
        tools=[_DelegatingSubAgentTool(delegate)],
        observability_port=agent_test_observability_port(),
        runtime_settings=agent_test_runtime_settings(),
    )
    events = [
        event
        async for event in agent._run_tool_iteration_loop(
            {
                "tool_calls": [{
                    "id": "call1",
                    "function": {
                        "name": "delegate_subtask",
                        "arguments": {"goal": "search KB"},
                    },
                }]
            },
            None,
            emit_deltas=False,
            response_schema=None,
        )
    ]

    tool_event = next(item for item in events if isinstance(item, ToolEvent))
    final_event = next(
        item for item in events if isinstance(item, MessageEvent)
    )
    replayed = MessageEvent.model_validate(
        upgrade_event_payload(final_event.model_dump(mode="json"))
    )

    assert tool_event.citations == [citation]
    assert final_event.citations == [citation]
    assert replayed.citations == [citation]
    assert EventMapper.event_to_sse_event(final_event).data.model_dump(
        mode="json"
    )["citations"] == [citation.model_dump(mode="json")]
