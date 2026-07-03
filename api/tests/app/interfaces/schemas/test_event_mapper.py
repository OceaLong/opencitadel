#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from app.domain.models.error_codes import MODEL_QUOTA_EXCEEDED
from app.domain.models.event import (
    AssistantNoticeEvent,
    ClarifyEvent,
    ClarifyOption,
    ClarifyQuestion,
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


def test_session_status_sse_mapping_allows_cancelled():
    event = SessionStatusEvent(status="cancelled")
    sse = EventMapper.event_to_sse_event(event)
    assert sse.event == "session_status"
    assert sse.data.status == "cancelled"


def test_all_event_types_have_typed_sse_mapping_and_meta():
    samples = [
        MessageEvent(role="user", message="hi"),
        MessageDeltaEvent(stream_id="stream-1", delta="hello"),
        ReasoningDeltaEvent(stream_id="stream-1", delta="thinking"),
        ToolArgsDeltaEvent(stream_id="stream-1", tool_call_id="tool-1", delta="{}"),
        AssistantNoticeEvent(message="notice"),
        SessionStatusEvent(status="running"),
        DebugItemEvent(item_type="planner_output", payload={"title": "t"}),
        ClarifyEvent(
            title="需要确认",
            questions=[
                ClarifyQuestion(
                    id="scope",
                    prompt="选择范围",
                    options=[ClarifyOption(id="api", label="API")],
                )
            ],
        ),
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


def test_error_sse_mapping_preserves_code():
    event = ErrorEvent(error="quota exhausted", code=MODEL_QUOTA_EXCEEDED)
    sse = EventMapper.event_to_sse_event(event)
    assert sse.event == "error"
    assert sse.data.error == "quota exhausted"
    assert sse.data.code == MODEL_QUOTA_EXCEEDED


def test_clarify_sse_mapping_projects_questions():
    event = ClarifyEvent(
        title="需要确认几个关键点",
        questions=[
            ClarifyQuestion(
                id="scope",
                prompt="选择实现范围",
                options=[
                    ClarifyOption(id="backend", label="后端"),
                    ClarifyOption(id="frontend", label="前端"),
                ],
                allow_multiple=True,
                allow_custom=True,
            )
        ],
    )

    sse = EventMapper.event_to_sse_event(event)

    assert sse.event == "clarify"
    assert sse.data.title == "需要确认几个关键点"
    assert sse.data.questions[0].id == "scope"
    assert sse.data.questions[0].allow_multiple is True
    assert sse.data.questions[0].options[1].label == "前端"


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


def test_observability_fields_are_projected_for_step_and_tool():
    started_at = datetime.now()
    ended_at = started_at + timedelta(seconds=2)
    step_event = StepEvent(
        step=Step(id="s1", description="step"),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=2000,
        error="step failed",
    )
    tool_event = ToolEvent(
        tool_call_id="tool-1",
        tool_name="shell",
        function_name="run",
        function_args={"cmd": "ls"},
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=2000,
        error="tool failed",
        span_id="tool:tool-1",
        parent_span_id="step:s1",
    )

    step_sse = EventMapper.event_to_sse_event(step_event)
    tool_sse = EventMapper.event_to_sse_event(tool_event)

    assert step_sse.data.duration_ms == 2000
    assert step_sse.data.error == "step failed"
    assert tool_sse.data.duration_ms == 2000
    assert tool_sse.data.error == "tool failed"
    assert tool_sse.data.span_id == "tool:tool-1"
    assert tool_sse.data.parent_span_id == "step:s1"


def test_debug_projection_includes_debug_items_only_when_requested():
    events = [
        MessageEvent(role="assistant", message="hi"),
        DebugItemEvent(item_type="planner_output", payload={"title": "t"}),
        ReasoningDeltaEvent(stream_id="stream-1", delta="thinking"),
    ]

    default_projection = EventMapper.events_to_sse_events(events, include_transient=True)
    debug_projection = EventMapper.events_to_sse_events(
        events,
        include_transient=True,
        include_debug=True,
    )

    assert [item.event for item in default_projection] == ["message"]
    assert [item.event for item in debug_projection] == [
        "message",
        "debug_item",
        "reasoning_delta",
    ]
