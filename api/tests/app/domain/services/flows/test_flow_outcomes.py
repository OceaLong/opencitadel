#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest

from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, WaitEvent
from app.domain.models.message import Message
from app.domain.models.session import Session, SessionStatus
from app.domain.services.flows.base import FlowStatus
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.domain.services.flows.doc_qa_flow import DocQAFlow
from app.domain.services.flows.hybrid_ask_flow import HybridAskFlow
from app.domain.services.flows.planner_react import PLAN_APPROVAL_PHASE, PlannerReActFlow


class _Agent:
    def __init__(
            self,
            *,
            events: list[BaseEvent] | None = None,
            error: BaseException | None = None,
    ) -> None:
        self._events = events or []
        self._error = error

    def set_locale(self, _locale: str) -> None:
        return None

    async def invoke(self, *_args, **_kwargs) -> AsyncGenerator[BaseEvent, None]:
        for event in self._events:
            yield event
        if self._error:
            raise self._error


def _ask_flow(flow_cls, *, events=None, error=None):
    flow = flow_cls.__new__(flow_cls)
    flow.status = FlowStatus.EXECUTING
    flow._agent = _Agent(events=events, error=error)
    return flow


async def _collect(flow) -> list[BaseEvent]:
    events = []
    async for event in flow.invoke(Message(message="question")):
        events.append(event)
    return events


@pytest.mark.parametrize("flow_cls", [CodeAskFlow, DocQAFlow, HybridAskFlow])
def test_ask_flow_normal_completion_has_succeeded_outcome(flow_cls):
    flow = _ask_flow(flow_cls)

    asyncio.run(_collect(flow))

    assert getattr(getattr(flow, "outcome", None), "status", None) == "succeeded"


@pytest.mark.parametrize("flow_cls", [CodeAskFlow, DocQAFlow, HybridAskFlow])
def test_ask_flow_error_event_has_failed_outcome(flow_cls):
    flow = _ask_flow(flow_cls, events=[ErrorEvent(error="agent failed", code="AGENT_FAILED")])

    asyncio.run(_collect(flow))

    outcome = getattr(flow, "outcome", None)
    assert getattr(outcome, "status", None) == "failed"
    assert getattr(getattr(outcome, "error", None), "message", None) == "agent failed"


@pytest.mark.parametrize("flow_cls", [CodeAskFlow, DocQAFlow, HybridAskFlow])
def test_ask_flow_approval_wait_ends_epoch_without_done(flow_cls):
    flow = _ask_flow(flow_cls, events=[WaitEvent(reason="tool_approval")])

    events = asyncio.run(_collect(flow))

    assert getattr(getattr(flow, "outcome", None), "status", None) == "waiting"
    assert not any(isinstance(event, DoneEvent) for event in events)


@pytest.mark.parametrize("flow_cls", [CodeAskFlow, DocQAFlow, HybridAskFlow])
def test_ask_flow_exception_has_failed_outcome(flow_cls):
    flow = _ask_flow(flow_cls, error=RuntimeError("agent exploded"))

    asyncio.run(_collect(flow))

    outcome = getattr(flow, "outcome", None)
    assert getattr(outcome, "status", None) == "failed"
    assert getattr(getattr(outcome, "error", None), "message", None) == "agent exploded"


@pytest.mark.parametrize("flow_cls", [CodeAskFlow, DocQAFlow, HybridAskFlow])
def test_ask_flow_cancellation_has_cancelled_outcome(flow_cls):
    flow = _ask_flow(flow_cls, error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_collect(flow))

    assert getattr(getattr(flow, "outcome", None), "status", None) == "cancelled"


class _SessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_by_id(self, _session_id: str) -> Session:
        return self._session


class _Uow:
    def __init__(self, session: Session) -> None:
        self.session = _SessionRepository(session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Tracer:
    def span(self, _name: str):
        return nullcontext()


def test_plan_approval_wait_has_waiting_outcome():
    session = Session(
        id="session-1",
        status=SessionStatus.WAITING,
        pending_phase=PLAN_APPROVAL_PHASE,
        pending_metadata={},
    )
    flow = PlannerReActFlow.__new__(PlannerReActFlow)
    flow._session_id = session.id
    flow._uow_factory = lambda: _Uow(session)
    flow._observability = SimpleNamespace(
        create_agent_tracer=lambda *_args: _Tracer(),
    )

    events = asyncio.run(_collect(flow))

    assert any(isinstance(event, WaitEvent) for event in events)
    assert getattr(getattr(flow, "outcome", None), "status", None) == "waiting"
