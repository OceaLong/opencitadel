#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import MagicMock

from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from tests.app.domain.services.agents.conftest import agent_test_observability_port


class _SuccessStreamLLM:
    model_name = "test-model"
    supports_multimodal = False

    @property
    def capabilities(self):
        return ModelCapabilities()

    async def stream_invoke(self, *args, **kwargs):
        yield {"content": "ok"}


class _StubAgent(BaseAgent):
    name = "stub"
    _system_prompt = "test"


def _make_agent():
    return _StubAgent(
        uow_factory=MagicMock(),
        session_id="sess-1",
        agent_config=AgentConfig(max_iterations=100, max_retries=3),
        llm=_SuccessStreamLLM(),
        json_parser=MagicMock(),
        tools=[],
        observability_port=agent_test_observability_port(),
        runtime_settings=AgentRuntimeSettings(
            memory=AgentMemoryRuntimeSettings(
                compact_always_on_step_boundary=False,
                compact_rule_trigger_threshold=999999,
                compact_strategy="rule",
            ),
        ),
    )


def test_many_successful_llm_calls_do_not_exhaust_retry_budget():
    agent = _make_agent()
    agent._memory = Memory(messages=[{"role": "system", "content": "stable prefix"}])

    async def _run():
        for i in range(15):
            async for _event in agent._invoke_llm([{"role": "user", "content": f"msg {i}"}]):
                pass

    asyncio.run(_run())
    assert agent._retry_budget.used_calls == 0
