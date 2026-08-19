#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from contextvars import Context
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.services.agent_service import AgentService
from app.domain.models.resource_governance import (
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.session import Session, SessionStatus


class _InputStream:
    def __init__(self) -> None:
        self.payloads = []

    async def put(self, payload):
        self.payloads.append(payload)
        return f"input-{len(self.payloads)}"


class _Task:
    def __init__(self) -> None:
        self.id = "task-1"
        self.input_stream = _InputStream()
        self.output_stream = SimpleNamespace()

    async def invoke(self):
        return None


class _TaskClass:
    task = _Task()

    @classmethod
    async def get(cls, task_id):
        assert task_id == cls.task.id
        return cls.task


class _SessionRepository:
    def __init__(self) -> None:
        self.session = Session(
            id="s1",
            task_id="task-1",
            owner_user_id="u1",
            status=SessionStatus.RUNNING,
        )
        self.persisted = []

    async def get_by_id(self, session_id):
        assert session_id == "s1"
        return self.session

    async def update_latest_message(self, **_kwargs):
        return None

    async def update_unread_message_count(self, _session_id, _count):
        return None

    async def add_event(self, _session_id, event, seq=None):
        assert seq is not None
        self.persisted.append(event.model_copy(deep=True))
        return seq


class _Uow:
    def __init__(self, binding) -> None:
        self.session = _SessionRepository()
        self.file = SimpleNamespace(list_by_ids=AsyncMock(return_value=[]))
        self.resource_governance = SimpleNamespace(
            list_current_bindings=AsyncMock(return_value=[binding])
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_user_message_captures_current_binding_snapshot_once_per_turn():
    binding = SessionResourceBinding(
        id="binding-v1",
        session_id="s1",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="kbv1",
        bound_by="u1",
    )
    uow = _Uow(binding)
    service = AgentService(
        uow_factory=lambda: uow,
        task_cls=_TaskClass,
        checkpoint_service=SimpleNamespace(),
        task_state_port=SimpleNamespace(
            get_runtime_snapshot=AsyncMock(
                return_value={
                    "cancelled": False,
                    "is_done": False,
                }
            )
        ),
        event_sequence_port=SimpleNamespace(
            allocate=AsyncMock(return_value=17)
        ),
    )

    stream = service.chat("s1", message="question")
    event = await stream.__anext__()
    await stream.aclose()

    expected = [
        {
            "binding_id": "binding-v1",
            "resource_kind": "knowledge_base",
            "resource_id": "kb1",
            "version_id": "kbv1",
        }
    ]
    assert [
        item.model_dump(mode="json")
        for item in event.resource_bindings
    ] == expected
    assert [
        item.model_dump(mode="json")
        for item in uow.session.persisted[0].resource_bindings
    ] == expected
    uow.resource_governance.list_current_bindings.assert_awaited_once_with(
        "s1"
    )


@pytest.mark.asyncio
async def test_chat_close_from_fresh_context_does_not_persist_false_error():
    binding = SessionResourceBinding(
        id="binding-v1",
        session_id="s1",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="kbv1",
        bound_by="u1",
    )
    uow = _Uow(binding)
    service = AgentService(
        uow_factory=lambda: uow,
        task_cls=_TaskClass,
        checkpoint_service=SimpleNamespace(),
        task_state_port=SimpleNamespace(
            get_runtime_snapshot=AsyncMock(
                return_value={
                    "cancelled": False,
                    "is_done": False,
                }
            )
        ),
        event_sequence_port=SimpleNamespace(
            allocate=AsyncMock(side_effect=[17, 18])
        ),
    )

    stream = service.chat("s1", message="question")
    await stream.__anext__()

    close_task = Context().run(asyncio.create_task, stream.aclose())
    await close_task

    assert len(uow.session.persisted) == 1
