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
        compact_always_on_step_boundary=overrides.pop("compact_always_on_step_boundary", True),
        compact_rule_trigger_threshold=overrides.pop("compact_rule_trigger_threshold", 16000),
        tool_output_offload_enabled=overrides.pop("tool_output_offload_enabled", False),
        tool_output_offload_threshold_chars=overrides.pop("tool_output_offload_threshold_chars", 4000),
    )
    return AgentRuntimeSettings(
        tool_timeout_seconds=overrides.pop("tool_timeout_seconds", 120),
        gate_profile=overrides.pop("gate_profile", None),
        operator_domains=overrides.pop("operator_domains", []),
        tool_gate_call_level_enabled=overrides.pop("tool_gate_call_level_enabled", None),
        memory=memory,
        **overrides,
    )


def agent_test_observability_port():
    return _NoopObservability()
