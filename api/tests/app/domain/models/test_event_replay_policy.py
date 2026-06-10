#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from app.domain.models.event import MessageEvent
from app.domain.models.event_policy import should_persist_event, should_project_event


def test_message_event_should_persist_and_project():
    event = MessageEvent(role="user", message="hello", created_at=datetime.now(timezone.utc))
    assert should_persist_event(event) is True
    assert should_project_event(event, include_transient=False) is True


def test_transient_delta_not_persisted():
    from app.domain.models.event import MessageDeltaEvent

    event = MessageDeltaEvent(stream_id="s1", delta="hi")
    assert should_persist_event(event) is False
    assert should_project_event(event, include_transient=False) is False
    assert should_project_event(event, include_transient=True) is True
