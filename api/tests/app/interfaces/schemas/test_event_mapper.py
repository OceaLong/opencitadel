#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import (
    MessageDeltaEvent,
    MessageEvent,
    AssistantNoticeEvent,
    SessionStatusEvent,
)
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
