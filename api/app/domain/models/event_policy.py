#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event visibility, persistence, and timeline projection policies."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.models.event import BaseEvent

EVENT_SCHEMA_VERSION = 2

TRANSIENT_EVENT_TYPES = frozenset({
    "message_delta",
    "reasoning_delta",
    "tool_args_delta",
})

DEBUG_EVENT_TYPES = frozenset({
    "debug_item",
})

TIMELINE_EVENT_TYPES = frozenset({
    "clarify",
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
    """Whether an event should be appended to session_events table."""
    if event.type in TRANSIENT_EVENT_TYPES:
        return False
    if getattr(event, "persist", True) is False:
        return False
    visibility = _value(getattr(event, "visibility", "user"))
    if visibility == "internal":
        return False
    return True


def should_project_event(
        event: "BaseEvent",
        *,
        include_transient: bool = True,
        include_debug: bool = False,
        include_internal: bool = False,
) -> bool:
    """Whether an event should be sent to a client-facing event projection."""
    if event.type in TRANSIENT_EVENT_TYPES:
        if not include_transient:
            return False
        if event.type == "message_delta":
            return True
        return include_debug

    visibility = _value(getattr(event, "visibility", "user"))
    if visibility == "internal":
        return include_internal
    if visibility == "debug":
        return include_debug
    return True


def is_user_timeline_event(event: "BaseEvent") -> bool:
    """Whether an event should appear in the main chat timeline."""
    if event.type in TRANSIENT_EVENT_TYPES:
        return False
    if event.type in DEBUG_EVENT_TYPES:
        return False
    visibility = _value(getattr(event, "visibility", "user"))
    if visibility in {"internal", "debug"}:
        return False
    if event.type == "message":
        role = getattr(event, "role", "assistant")
        return role in {"user", "assistant"}
    return event.type in TIMELINE_EVENT_TYPES


def project_events(
        events: list,
        *,
        include_transient: bool = False,
        include_debug: bool = False,
        include_internal: bool = False,
) -> list:
    """Project events for live/replay client delivery."""
    return [
        event
        for event in events
        if should_project_event(
            event,
            include_transient=include_transient,
            include_debug=include_debug,
            include_internal=include_internal,
        )
    ]


def filter_events_for_session_replay(events: list) -> list:
    """Drop transient/internal events when replaying persisted session history."""
    return project_events(events, include_transient=False)


def _value(value):
    return value.value if hasattr(value, "value") else value
