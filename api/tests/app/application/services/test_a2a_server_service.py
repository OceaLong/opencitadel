from types import SimpleNamespace

import pytest

from app.application.services.a2a_server_service import (
    A2AServerService,
    build_a2a_text_response,
    extract_text_from_a2a_params,
)
from app.domain.models.scope import Principal
from app.domain.models.session import Session
from app.domain.models.user import GlobalRole
from app.domain.runtime_policy import ExecutionPolicy
from tests.app.application_test_support import FakeCircuitBreaker


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
    async def chat(self, session_id: str, *, owner_scope, message: str, request_id):
        assert request_id is not None
        yield type(
            "Event",
            (),
            {
                "event_type": "message",
                "payload": {"role": "assistant", "message": f"ok:{message}"},
            },
        )()
        yield type("Event", (), {"event_type": "done", "payload": {}})()


class _FakeSkillService:
    async def list_skills(self, enabled_only: bool = False):
        return []


class _FakeModelService:
    async def resolve_chat(self, *, scope):
        assert scope.user_id == "owner-1"
        return type("Model", (), {"id": "model-1"})()


class _PolicyHeads:
    async def active_execution(self, **_kwargs):
        return SimpleNamespace(revision=SimpleNamespace(policy=ExecutionPolicy()))


@pytest.mark.asyncio
async def test_a2a_message_send_creates_owned_session():
    session_service = _FakeSessionService()
    service = A2AServerService(
        agent_service=_FakeAgentService(),
        session_service=session_service,
        skill_service=_FakeSkillService(),
        inference_model_service=_FakeModelService(),
        policy_heads=_PolicyHeads(),
        breaker=FakeCircuitBreaker(),
    )

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
