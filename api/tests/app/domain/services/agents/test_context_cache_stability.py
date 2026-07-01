#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from tests.app.domain.services.agents.conftest import agent_test_observability_port


class _StubAgent(BaseAgent):
    name = "stub"
    _system_prompt = "test"

    async def run(self):
        return None


@pytest.fixture
def stub_agent():
    uow_factory = MagicMock()
    llm = AsyncMock()
    llm.model_name = "test"
    llm.supports_multimodal = False
    json_parser = AsyncMock()
    agent = _StubAgent(
        uow_factory=uow_factory,
        session_id="sess-1",
        agent_config=AgentConfig(max_iterations=3, max_retries=2),
        llm=llm,
        json_parser=json_parser,
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
    agent._memory = Memory(messages=[{"role": "system", "content": "stable prefix"}])
    return agent


def test_summarize_and_compact_skips_when_gated(stub_agent):
    before = json.dumps(stub_agent._memory.get_messages(), sort_keys=True)

    async def _run():
        await stub_agent.summarize_and_compact()

    asyncio.run(_run())
    after = json.dumps(stub_agent._memory.get_messages(), sort_keys=True)
    assert before == after


def test_messages_for_llm_deterministic_key_order():
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": "prefix"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi", "tool_calls": [{"id": "1", "function": {"name": "x"}}]},
    ]
    a = json.dumps(BaseAgent._messages_for_llm(messages), sort_keys=True)
    b = json.dumps(BaseAgent._messages_for_llm(messages), sort_keys=True)
    assert a == b


def test_current_memory_token_estimate_ignores_stale_last_prompt(stub_agent):
    stub_agent._last_prompt_tokens = 999999
    assert stub_agent._estimate_current_memory_tokens() < stub_agent._estimate_memory_tokens()


def test_append_only_prefix_between_rounds():
    round1 = [
        {"role": "system", "content": "prefix"},
        {"role": "user", "content": "q1"},
    ]
    round2 = round1 + [{"role": "assistant", "content": "a1"}]
    prefix1 = json.dumps(BaseAgent._messages_for_llm(round1), sort_keys=True)
    prefix2 = json.dumps(BaseAgent._messages_for_llm(round2)[: len(round1)], sort_keys=True)
    # First N messages unchanged when appending assistant reply
    assert prefix1 == json.dumps(BaseAgent._messages_for_llm(round2[:-1]), sort_keys=True)
