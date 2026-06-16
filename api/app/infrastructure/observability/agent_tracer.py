#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Langfuse / Phoenix agent tracing helpers (uses OTel when configured)."""
import logging
from typing import Optional

from app.application.services.config_provider import get_runtime_config

logger = logging.getLogger(__name__)


class AgentTracer:
    """Lightweight agent step tracer backed by OpenTelemetry spans."""

    def __init__(self, session_id: str, agent_name: str = "") -> None:
        self._session_id = session_id
        self._agent_name = agent_name
        observability = get_runtime_config().observability
        self._enabled = observability.langfuse_enabled or observability.otel_enabled
        self._tracer = None
        if self._enabled:
            try:
                from app.infrastructure.observability.otel import get_tracer
                self._tracer = get_tracer("my-manus.agent")
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

    def record_llm_call(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        from app.infrastructure.observability.otel import record_llm_tokens
        record_llm_tokens(model, prompt_tokens, completion_tokens)
        if get_runtime_config().observability.langfuse_enabled:
            logger.debug(
                "Langfuse LLM trace session=%s agent=%s model=%s prompt=%s completion=%s",
                self._session_id,
                self._agent_name,
                model,
                prompt_tokens,
                completion_tokens,
            )
