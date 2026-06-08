#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import (
    AssistantNoticeEvent,
    DebugItemEvent,
    DoneEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEvent,
    PlanEvent,
    ReasoningDeltaEvent,
    SessionStatusEvent,
    StepEvent,
    TitleEvent,
    ToolArgsDeltaEvent,
    ToolEvent,
    UsageEvent,
    WaitEvent,
)
from app.domain.models.event_policy import EVENT_SCHEMA_VERSION
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.plan import Plan, Step
from app.interfaces.schemas.event import EventMapper


def test_event_mapper_filters_transient_on_replay():
    events = [
        MessageEvent(role="user", message="hi"),
        MessageDeltaEvent(stream_id="s1", delta='{"steps":[]}'),
        AssistantNoticeEvent(message="notice"),
    ]
    replay = EventMapper.events_to_sse_events(events, include_transient=False)
    types = [item.event for item in replay]
    assert "message" in types
    assert "assistant_notice" in types
    assert "message_delta" not in types


def test_session_status_sse_mapping():
    event = SessionStatusEvent(status="running")
    sse = EventMapper.event_to_sse_event(event)
    assert sse.event == "session_status"
    assert sse.data.status == "running"


def test_all_event_types_have_typed_sse_mapping_and_meta():
    samples = [
        MessageEvent(role="user", message="hi"),
        MessageDeltaEvent(stream_id="stream-1", delta="hello"),
        ReasoningDeltaEvent(stream_id="stream-1", delta="thinking"),
        ToolArgsDeltaEvent(stream_id="stream-1", tool_call_id="tool-1", delta="{}"),
        AssistantNoticeEvent(message="notice"),
        SessionStatusEvent(status="running"),
        DebugItemEvent(item_type="planner_output", payload={"title": "t"}),
        TitleEvent(title="title"),
        PlanEvent(plan=Plan(title="t", goal="g", steps=[Step(id="s1", description="step")])),
        StepEvent(step=Step(id="s1", description="step")),
        ToolEvent(tool_call_id="tool-1", tool_name="shell", function_name="run", function_args={}),
        WaitEvent(),
        UsageEvent(),
        DoneEvent(),
        ErrorEvent(error="boom"),
    ]

    for event in samples:
        sse = EventMapper.event_to_sse_event(event)
        assert sse.event == event.type
        assert sse.__class__.__name__ != "CommonSSEEvent"
        assert sse.data.event_id == event.id
        assert sse.data.schema_version == EVENT_SCHEMA_VERSION
        assert sse.data.visibility in {"user", "internal", "debug"}
        assert sse.data.channel in {"ui", "runtime", "debug"}
        assert isinstance(sse.data.persist, bool)


def test_event_upgrader_adds_v2_metadata_defaults():
    payload = {
        "id": "legacy-id",
        "type": "message_delta",
        "created_at": "2026-06-08T00:00:00",
        "stream_id": "stream-1",
        "delta": "hello",
    }

    upgraded = upgrade_event_payload(payload)

    assert upgraded["schema_version"] == EVENT_SCHEMA_VERSION
    assert upgraded["visibility"] == "internal"
    assert upgraded["channel"] == "runtime"
    assert upgraded["persist"] is False
