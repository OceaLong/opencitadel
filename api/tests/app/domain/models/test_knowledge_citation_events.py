#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import MessageEvent, ToolEvent, ToolEventStatus
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.tool_result import ToolResult, normalize_tool_result
from app.interfaces.schemas.event import EventMapper


def _citation() -> KnowledgeCitation:
    return KnowledgeCitation(
        version_id="kbv1",
        document_revision_id="revision1",
        doc_id="doc1",
        page_no=2,
        chunk_id="chunk1",
    )


def test_tool_result_normalization_preserves_structured_citations():
    result = normalize_tool_result(
        ToolResult(data="answer", citations=[_citation()])
    )
    assert result.citations == [_citation()]


def test_tool_event_derives_citations_from_trusted_tool_result_and_sse_maps_them():
    event = ToolEvent(
        tool_call_id="call1",
        tool_name="knowledge_base",
        function_name="kb_search",
        function_args={"query": "policy"},
        function_result=ToolResult(data="answer", citations=[_citation()]),
        status=ToolEventStatus.CALLED,
    )

    assert event.citations == [_citation()]
    assert EventMapper.event_to_sse_event(event).data.model_dump(
        mode="json"
    )["citations"] == [_citation().model_dump(mode="json")]


def test_message_citations_survive_persistence_replay_and_sse_mapping():
    original = MessageEvent(
        role="assistant",
        message="answer",
        citations=[_citation()],
    )
    replayed = MessageEvent.model_validate(
        upgrade_event_payload(original.model_dump(mode="json"))
    )

    assert replayed.citations == [_citation()]
    assert EventMapper.event_to_sse_event(replayed).data.model_dump(
        mode="json"
    )["citations"] == [_citation().model_dump(mode="json")]


def test_legacy_tool_and_message_payloads_default_citations_to_empty():
    tool = upgrade_event_payload(
        {
            "type": "tool",
            "tool_call_id": "call1",
            "tool_name": "knowledge_base",
            "function_name": "kb_search",
            "function_args": {},
        }
    )
    message = upgrade_event_payload(
        {"type": "message", "role": "assistant", "message": "legacy"}
    )

    assert ToolEvent.model_validate(tool).citations == []
    assert MessageEvent.model_validate(message).citations == []
