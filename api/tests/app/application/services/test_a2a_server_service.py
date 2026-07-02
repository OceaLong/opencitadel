#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.a2a_server_service import (
    A2AServerService,
    build_a2a_text_response,
    extract_text_from_a2a_params,
)
from app.domain.models.event import DoneEvent, MessageEvent
from app.domain.models.scope import Principal
from app.domain.models.session import Session
from app.domain.models.user import GlobalRole


def test_extract_text_from_a2a_params():
    params = {
        "message": {
            "parts": [
                {"kind": "text", "text": "hello"},
                {"kind": "text", "text": "world"},
            ],
        },
    }
    assert extract_text_from_a2a_params(params) == "hello\nworld"


def test_build_a2a_text_response():
    response = build_a2a_text_response("req-1", "done")
    assert response["id"] == "req-1"
    assert response["result"]["message"]["parts"][0]["text"] == "done"


class _FakeSessionService:
    def __init__(self):
        self.scope = None

    async def create_session(self, title: str, scope=None, **_kwargs):
        self.scope = scope
        return Session(id="session-1", title=title, owner_user_id=scope.user_id if scope else None)


class _FakeAgentService:
    async def chat(self, session_id: str, message: str):
        yield MessageEvent(role="assistant", message=f"ok:{message}")
        yield DoneEvent()


class _FakeSkillService:
    async def list_skills(self, enabled_only: bool = False):
        return []


class _FakeModelService:
    async def get_default_model(self):
        return type("Model", (), {"id": "model-1"})()


async def _closed_circuit(_model_id: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_a2a_message_send_creates_owned_session():
    session_service = _FakeSessionService()
    service = A2AServerService(
        agent_service=_FakeAgentService(),
        session_service=session_service,
        skill_service=_FakeSkillService(),
        llm_model_service=_FakeModelService(),
    )
    service._breaker.is_open = _closed_circuit

    response = await service.handle_message_send(
        {
            "id": "req-1",
            "params": {"message": {"parts": [{"kind": "text", "text": "hello"}]}},
        },
        principal=Principal(user_id="owner-1", global_role=GlobalRole.USER),
    )

    assert response["id"] == "req-1"
    assert session_service.scope is not None
    assert session_service.scope.user_id == "owner-1"
    assert session_service.scope.team_id is None
