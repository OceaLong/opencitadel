#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Langfuse / Phoenix agent tracing helpers (uses OTel when configured)."""
from typing import Optional

from app.application.services.config_provider import get_runtime_config

class AgentTracer:
    """Lightweight agent step tracer backed by OpenTelemetry spans."""

    def __init__(self, session_id: str, agent_name: str = "") -> None:
        self._session_id = session_id
        self._agent_name = agent_name
        observability = get_runtime_config().observability
        self._enabled = observability.otel_enabled
        self._tracer = None
        if self._enabled:
            try:
                from app.infrastructure.observability.otel import get_tracer
                self._tracer = get_tracer("opencitadel.agent")
            except Exception:
                self._enabled = False

    def span(self, name: str, attributes: Optional[dict] = None):
        if not self._enabled or not self._tracer:
            from contextlib import nullcontext
            return nullcontext()
        attrs = {
            "session_id": self._session_id,
            "agent_name": self._agent_name,
            **(attributes or {}),
        }
        return self._tracer.start_as_current_span(name, attributes=attrs)
