#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings


class _NoopObservability:
    def record_agent_cancel(self, session_id: str) -> None:
        return None

    def record_llm_tokens(
            self,
            model: str,
            *,
            prompt_tokens: int,
            completion_tokens: int,
            cached_tokens: int = 0,
    ) -> None:
        return None

    def record_agent_step(self, agent_name: str, step: str) -> None:
        return None

    def create_agent_tracer(self, session_id: str, agent_name: str):
        class _Tracer:
            def span(self, _name: str):
                from contextlib import nullcontext
                return nullcontext()

        return _Tracer()


class _DummyLLM:
    model_name = "test-model"
    supports_multimodal = False


def agent_test_runtime_settings(**overrides) -> AgentRuntimeSettings:
    memory = AgentMemoryRuntimeSettings(
        compact_tool_content_max_chars=overrides.pop("compact_tool_content_max_chars", 4000),
        compact_strategy=overrides.pop("compact_strategy", "rule"),
        compact_token_threshold=overrides.pop("compact_token_threshold", 100000),
        compact_keep_recent=overrides.pop("compact_keep_recent", 12),
    )
    return AgentRuntimeSettings(
        tool_timeout_seconds=overrides.pop("tool_timeout_seconds", 120),
        memory=memory,
    )


def agent_test_observability_port():
    return _NoopObservability()
