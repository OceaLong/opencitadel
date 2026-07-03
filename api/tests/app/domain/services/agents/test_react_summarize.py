#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from typing import AsyncGenerator, Optional, List

import pytest

from app.domain.models.event import MessageEvent, StepEvent
from app.domain.models.memory import Memory
from app.domain.models.message import Message, VisionAttachment
from app.domain.services.agents.react import ReActAgent
from tests.app.domain.services.agents.conftest import (
    _DummyLLM,
    agent_test_observability_port,
    agent_test_runtime_settings,
)


class _FakeSessionRepo:
    async def get_by_id(self, _session_id):
        return None


class _FakeUow:
    session = _FakeSessionRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeJsonParser:
    async def invoke(self, raw: str):
        return json.loads(raw)


class _SummarizeAgent(ReActAgent):
    async def invoke(
            self,
            query: str,
            format: Optional[str] = None,
            vision_attachments: Optional[List[VisionAttachment]] = None,
            emit_deltas: bool = True,
            response_schema=None,
    ) -> AsyncGenerator[MessageEvent, None]:
        assert vision_attachments is None
        assert response_schema is not None
        yield MessageEvent(role="assistant", message='{"message":"done","attachments":[]}')


class _StepAgent(ReActAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_vision_counts: List[int] = []

    async def invoke(
            self,
            query: str,
            format: Optional[str] = None,
            vision_attachments: Optional[List[VisionAttachment]] = None,
            emit_deltas: bool = True,
            response_schema=None,
    ) -> AsyncGenerator[MessageEvent, None]:
        self.received_vision_counts.append(len(vision_attachments or []))
        yield MessageEvent(role="assistant", message='{"success":true,"result":"ok","attachments":[]}')


class _RepairStepAgent(ReActAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repair_hints: List[str] = []

    async def invoke(
            self,
            query: str,
            format: Optional[str] = None,
            vision_attachments: Optional[List[VisionAttachment]] = None,
            emit_deltas: bool = True,
            response_schema=None,
    ) -> AsyncGenerator[MessageEvent, None]:
        if response_schema is not None:
            self.repair_hints.append(query)
            yield MessageEvent(
                role="assistant",
                message='{"success":true,"result":"fixed","attachments":[]}',
            )
            return
        yield MessageEvent(role="assistant", message='{"result":"incomplete"}')


def _agent_kwargs(**overrides):
    defaults = {
        "uow_factory": lambda: _FakeUow(),
        "session_id": "session-1",
        "agent_config": type("Cfg", (), {"max_retries": 1, "max_iterations": 1})(),
        "llm": _DummyLLM(),
        "json_parser": _FakeJsonParser(),
        "tools": [],
        "observability_port": agent_test_observability_port(),
        "runtime_settings": agent_test_runtime_settings(),
    }
    defaults.update(overrides)
    return defaults


def _build_agent() -> ReActAgent:
    return _SummarizeAgent(**_agent_kwargs())


async def _test_summarize_accepts_user_message_and_vision_attachments():
    agent = _build_agent()
    user_message = Message(
        message="summarize this",
        vision_attachments=[
            VisionAttachment(mime_type="image/png", data_base64="aW1n"),
        ],
    )

    events = [event async for event in agent.summarize(user_message)]
    assert len(events) == 1
    assert isinstance(events[0], MessageEvent)
    assert events[0].message == "done"


def test_summarize_accepts_user_message_and_vision_attachments():
    asyncio.run(_test_summarize_accepts_user_message_and_vision_attachments())


async def _test_execute_step_respects_optional_vision_attachments():
    from app.domain.models.plan import Plan, Step

    agent = _StepAgent(**_agent_kwargs())
    message = Message(
        message="do task",
        vision_attachments=[VisionAttachment(mime_type="image/png", data_base64="aW1n")],
    )
    plan = Plan(title="t", message="m", language="zh", steps=[Step(description="s1")])
    step = plan.steps[0]

    async for _ in agent.execute_step(plan, step, message, vision_attachments=message.vision_attachments):
        pass
    assert agent.received_vision_counts == [1]

    async for _ in agent.execute_step(plan, step, message, vision_attachments=None):
        pass
    assert agent.received_vision_counts == [1, 0]


def test_execute_step_respects_optional_vision_attachments():
    asyncio.run(_test_execute_step_respects_optional_vision_attachments())


async def _test_execute_step_uses_locale_aware_structured_repair_hint(language: str, expected_snippet: str):
    from app.domain.models.plan import Plan, Step

    agent = _RepairStepAgent(**_agent_kwargs())
    agent._memory = Memory(messages=[{"role": "system", "content": "test"}])
    message = Message(message="do task")
    plan = Plan(title="t", message="m", language=language, steps=[Step(description="s1")])
    step = plan.steps[0]

    events = [event async for event in agent.execute_step(plan, step, message)]
    assert len(agent.repair_hints) == 1
    assert expected_snippet in agent.repair_hints[0]
    assert any(isinstance(event, StepEvent) for event in events)
    assert step.result == "fixed"


@pytest.mark.parametrize(
    "language,expected_snippet",
    [
        ("zh", "上次输出不符合结构化 schema"),
        ("en", "Previous output did not match the required structured schema"),
    ],
)
def test_execute_step_uses_locale_aware_structured_repair_hint(language, expected_snippet):
    asyncio.run(_test_execute_step_uses_locale_aware_structured_repair_hint(language, expected_snippet))
