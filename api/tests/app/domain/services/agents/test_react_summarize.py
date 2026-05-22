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
    ) -> AsyncGenerator[MessageEvent, None]:
        assert vision_attachments is not None
        assert len(vision_attachments) == 1
        assert vision_attachments[0].mime_type == "image/png"
        yield MessageEvent(role="assistant", message='{"message":"done","attachments":[]}')


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
