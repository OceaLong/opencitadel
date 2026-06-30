#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event schema upgrade helpers."""
from typing import Any, Dict

from app.domain.models.event_policy import EVENT_SCHEMA_VERSION


def upgrade_event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade a persisted/raw event payload to the current domain schema."""
    upgraded = dict(payload)
    schema_version = int(upgraded.get("schema_version") or 1)

    if schema_version < 2:
        upgraded.setdefault("visibility", _default_visibility(upgraded.get("type", "")))
        upgraded.setdefault("channel", _default_channel(upgraded.get("type", "")))
        upgraded.setdefault("persist", _default_persist(upgraded.get("type", "")))
        schema_version = 2

    if upgraded.get("type") == "error":
        upgraded.setdefault("code", None)

    upgraded["schema_version"] = EVENT_SCHEMA_VERSION
    return upgraded


def _default_visibility(event_type: str) -> str:
    if event_type in {"reasoning_delta", "tool_args_delta", "debug_item"}:
        return "debug"
    if event_type == "message_delta":
        return "internal"
    return "user"


def _default_channel(event_type: str) -> str:
    if event_type in {"reasoning_delta", "tool_args_delta", "debug_item"}:
        return "debug"
    if event_type == "message_delta":
        return "runtime"
    return "ui"


def _default_persist(event_type: str) -> bool:
    return event_type not in {"message_delta", "reasoning_delta", "tool_args_delta"}
