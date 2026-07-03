#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import MagicMock

from pydantic import BaseModel

from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts import zh as zh_prompts
from tests.app.domain.services.agents.conftest import agent_test_observability_port


class _LengthThenOkLLM:
    model_name = "test-model"
    supports_multimodal = False

    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self):
        return ModelCapabilities()

    async def stream_invoke(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {"content": '{"message": "' + ("x" * 100)}
            yield {"finish_reason": "length"}
            return
        yield {"content": '{"message": "summary", "attachments": ["/home/ubuntu/report.md"]}'}


class _StubAgent(BaseAgent):
    name = "stub"
    _system_prompt = "test"


def _make_agent(llm: _LengthThenOkLLM) -> _StubAgent:
    return _StubAgent(
        uow_factory=MagicMock(),
        session_id="sess-1",
        agent_config=AgentConfig(max_iterations=100, max_retries=3),
        llm=llm,
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


def test_is_output_length_limited():
    assert BaseAgent._is_output_length_limited("length") is True
    assert BaseAgent._is_output_length_limited("max_tokens") is True
    assert BaseAgent._is_output_length_limited("MAX_TOKENS") is True
    assert BaseAgent._is_output_length_limited("stop") is False
    assert BaseAgent._is_output_length_limited(None) is False


def test_invoke_llm_retries_on_length_truncation_for_structured_output():
    llm = _LengthThenOkLLM()
    agent = _make_agent(llm)
    agent.set_locale("zh")
    agent._memory = Memory(messages=[{"role": "system", "content": "stable prefix"}])

    class _SummarySchema(BaseModel):
        message: str
        attachments: list[str] = []

    async def _run():
        async for _event in agent._invoke_llm(
            [{"role": "user", "content": "summarize"}],
            response_schema=_SummarySchema,
        ):
            pass

    asyncio.run(_run())

    assert llm.calls == 2
    assert agent._last_llm_message is not None
    assert "summary" in (agent._last_llm_message.get("content") or "")
    repair_messages = [
        message.get("content")
        for message in agent._memory.messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and "write_file" in message.get("content", "")
    ]
    assert repair_messages
    assert zh_prompts.internal.LENGTH_TRUNCATION_REPAIR_HINT.split("。")[0] in repair_messages[-1]
