#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json

from app.domain.models.event import ClarifyEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.services.agents.clarify import ClarifyAgent


class DummyJsonParser:
    async def invoke(self, text: str):
        return json.loads(text)


def test_clarify_agent_yields_clarify_event_when_questions_needed():
    agent = ClarifyAgent.__new__(ClarifyAgent)
    agent._json_parser = DummyJsonParser()
    agent.last_brief = None
    agent._writing_style_override = None
    agent._override_base_rules = False
    agent._system_prompt = ""

    async def fake_invoke(*args, **kwargs):
        yield MessageEvent(
            message=json.dumps({
                "needs_clarification": True,
                "title": "需要确认",
                "questions": [
                    {
                        "id": "scope",
                        "prompt": "选择范围",
                        "options": [{"id": "api", "label": "API"}],
                        "allow_multiple": False,
                        "allow_custom": True,
                    }
                ],
            })
        )

    agent.invoke = fake_invoke

    async def collect():
        return [event async for event in agent.analyze(Message(message="实现功能"))]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert isinstance(events[0], ClarifyEvent)
    assert events[0].questions[0].prompt == "选择范围"


def test_clarify_agent_records_brief_when_no_questions_needed():
    agent = ClarifyAgent.__new__(ClarifyAgent)
    agent._json_parser = DummyJsonParser()
    agent.last_brief = None
    agent._writing_style_override = None
    agent._override_base_rules = False
    agent._system_prompt = ""

    async def fake_invoke(*args, **kwargs):
        yield MessageEvent(
            message=json.dumps({
                "needs_clarification": False,
                "questions": [],
                "brief": "完整需求摘要",
            })
        )

    agent.invoke = fake_invoke

    async def collect():
        return [event async for event in agent.analyze(Message(message="明确需求"))]

    events = asyncio.run(collect())

    assert events == []
    assert agent.last_brief == "完整需求摘要"
