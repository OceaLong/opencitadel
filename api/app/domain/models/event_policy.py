#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event visibility, persistence, and timeline projection policies."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.models.event import BaseEvent

EVENT_SCHEMA_VERSION = 1

TRANSIENT_EVENT_TYPES = frozenset({
    "message_delta",
    "reasoning_delta",
    "tool_args_delta",
})

DEBUG_EVENT_TYPES = frozenset({
    "debug_item",
})

TIMELINE_EVENT_TYPES = frozenset({
    "message",
    "assistant_notice",
    "step",
    "tool",
    "error",
})

NON_TIMELINE_UI_EVENT_TYPES = frozenset({
    "title",
    "plan",
    "wait",
    "done",
    "usage",
    "session_status",
})


def should_persist_event(event: "BaseEvent") -> bool:
    """Whether an event should be appended to sessions.events."""
    if event.type in TRANSIENT_EVENT_TYPES:
        return False
    if getattr(event, "persist", True) is False:
        return False
    visibility = getattr(event, "visibility", "user")
    if visibility == "internal":
        return False
    return True


def is_user_timeline_event(event: "BaseEvent") -> bool:
    """Whether an event should appear in the main chat timeline."""
    if event.type in TRANSIENT_EVENT_TYPES:
        return False
    if event.type in DEBUG_EVENT_TYPES:
        return False
    visibility = getattr(event, "visibility", "user")
    if visibility in {"internal", "debug"}:
        return False
    if event.type == "message":
        role = getattr(event, "role", "assistant")
        return role in {"user", "assistant"}
    return event.type in TIMELINE_EVENT_TYPES


def filter_events_for_session_replay(events: list) -> list:
    """Drop transient/internal events when replaying persisted session history."""
    return [e for e in events if should_persist_event(e)]
