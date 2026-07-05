#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from typing import AsyncGenerator

import pytest

from app.domain.models.event import BaseEvent, DoneEvent
from app.domain.models.message import Message
from app.domain.services.flows.base import FlowStatus
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.domain.services.flows.doc_qa_flow import DocQAFlow
from app.domain.services.flows.hybrid_ask_flow import HybridAskFlow


class _CaptureAgent:
    def __init__(self) -> None:
        self.query = None
        self.kwargs = {}

    def set_locale(self, locale: str) -> None:
        return None

    async def invoke(self, query, **kwargs) -> AsyncGenerator[BaseEvent, None]:
        self.query = query
        self.kwargs = kwargs
        if False:
            yield


def _make_flow(flow_cls):
    flow = flow_cls.__new__(flow_cls)
    flow.status = FlowStatus.EXECUTING
    flow._agent = _CaptureAgent()
    return flow


async def _collect_invoke(flow, message: Message):
    events = []
    async for event in flow.invoke(message):
        events.append(event)
    return events


@pytest.mark.parametrize(
    "flow_cls",
    [CodeAskFlow, HybridAskFlow, DocQAFlow],
)
def test_ask_flows_pass_string_query_to_agent(flow_cls):
    flow = _make_flow(flow_cls)
    message = Message(message="分析核心功能")

    events = asyncio.run(_collect_invoke(flow, message))

    assert flow._agent.query == "分析核心功能"
    assert isinstance(flow._agent.query, str)
    assert flow._agent.kwargs.get("vision_attachments") is None
    assert any(isinstance(event, DoneEvent) for event in events)


def test_ask_flow_passes_vision_attachments():
    from app.domain.models.multimodal import MediaAttachment

    flow = _make_flow(CodeAskFlow)
    attachment = MediaAttachment(
        media_type="image",
        mime_type="image/png",
        data_base64="aGVsbG8=",
    )
    message = Message(message="describe image", vision_attachments=[attachment])

    asyncio.run(_collect_invoke(flow, message))

    assert flow._agent.query == "describe image"
    assert flow._agent.kwargs.get("vision_attachments") == [attachment]
