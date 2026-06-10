#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import SessionStatusEvent
from app.interfaces.schemas.event import EventMapper


def test_session_status_sse_mapping_allows_failed():
    event = SessionStatusEvent(status="failed")
    sse = EventMapper.event_to_sse_event(event)
    assert sse.event == "session_status"
    assert sse.data.status == "failed"
