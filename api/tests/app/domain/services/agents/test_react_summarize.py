#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from typing import AsyncGenerator, Optional, List

from app.domain.models.event import MessageEvent
from app.domain.models.message import Message, VisionAttachment
from app.domain.services.agents.react import ReActAgent


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
    ) -> AsyncGenerator[MessageEvent, None]:
        assert vision_attachments is None
        assert emit_deltas is False
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
    ) -> AsyncGenerator[MessageEvent, None]:
        assert emit_deltas is False
        self.received_vision_counts.append(len(vision_attachments or []))
        yield MessageEvent(role="assistant", message='{"success":true,"result":"ok","attachments":[]}')


def _build_agent() -> ReActAgent:
    return _SummarizeAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=type("Cfg", (), {"max_retries": 1, "max_iterations": 1})(),
        llm=object(),
        json_parser=_FakeJsonParser(),
        tools=[],
    )


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

    agent = _StepAgent(
        uow_factory=lambda: None,
        session_id="session-1",
        agent_config=type("Cfg", (), {"max_retries": 1, "max_iterations": 1})(),
        llm=object(),
        json_parser=_FakeJsonParser(),
        tools=[],
    )
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
