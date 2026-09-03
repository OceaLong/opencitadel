from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.services.a2a_server_service import (
    A2AServerService,
    build_a2a_text_response,
    extract_text_from_a2a_params,
)
from app.domain.execution.run import RunStatus
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
    assert "taskId" not in response["result"]["message"]


def test_build_a2a_text_response_carries_task_identity():
    """Without taskId/contextId in the reply, tasks/get + multi-turn are unreachable."""
    response = build_a2a_text_response("req-1", "done", task_id="session-1")
    message = response["result"]["message"]
    assert message["taskId"] == "session-1"
    assert message["contextId"] == "session-1"
    assert message["kind"] == "message"


class _FakeSessionService:
    def __init__(self, existing=None):
        self.scope = None
        self.create_calls = 0
        self._existing = existing

    async def create_session(self, title: str, scope=None, **_kwargs):
        self.scope = scope
        self.create_calls += 1
        return Session(id="session-1", title=title, owner_user_id=scope.user_id if scope else None)

    async def get_session(self, session_id: str, scope=None):
        if self._existing is not None and self._existing.id == session_id:
            return self._existing
        return None


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
    # The reply must hand the caller the task identity for get/cancel/multi-turn.
    assert response["result"]["message"]["taskId"] == "session-1"
    assert response["result"]["message"]["contextId"] == "session-1"


def _send_service(session_service):
    return A2AServerService(
        agent_service=_FakeAgentService(),
        session_service=session_service,
        skill_service=_FakeSkillService(),
        inference_model_service=_FakeModelService(),
        policy_heads=_PolicyHeads(),
        breaker=FakeCircuitBreaker(),
    )


@pytest.mark.asyncio
async def test_a2a_message_send_reuses_session_for_context_id():
    existing = Session(id="session-1", title="A2A Request", owner_user_id="owner-1")
    session_service = _FakeSessionService(existing=existing)
    service = _send_service(session_service)

    response = await service.handle_message_send(
        {
            "id": "req-2",
            "params": {
                "message": {
                    "contextId": "session-1",
                    "parts": [{"kind": "text", "text": "follow-up"}],
                },
            },
        },
        principal=Principal(user_id="owner-1", global_role=GlobalRole.USER),
    )

    assert session_service.create_calls == 0
    assert response["result"]["message"]["taskId"] == "session-1"


@pytest.mark.asyncio
async def test_a2a_message_send_unknown_context_id_is_task_not_found():
    session_service = _FakeSessionService(existing=None)
    service = _send_service(session_service)

    response = await service.handle_message_send(
        {
            "id": "req-3",
            "params": {
                "message": {
                    "contextId": "missing",
                    "parts": [{"kind": "text", "text": "follow-up"}],
                },
            },
        },
        principal=Principal(user_id="owner-1", global_role=GlobalRole.USER),
    )

    assert session_service.create_calls == 0
    assert response["error"]["code"] == -32001
    assert response["id"] == "req-3"


class _LookupSessionService:
    """Session service exposing only the read/stop-adjacent surface tasks use."""

    def __init__(self, session=None):
        self._session = session
        self.get_calls: list[tuple[str, object]] = []

    async def get_session(self, session_id: str, scope=None):
        self.get_calls.append((session_id, scope))
        return self._session


class _FakeRunProjection:
    def __init__(self, run_id=None, status=None):
        self._run_id = run_id
        self._status = status
        self.latest_calls: list[dict] = []
        self.status_calls: list[object] = []

    async def latest_active_run_id(self, *, source_entity_type, source_entity_id, owner_scope):
        self.latest_calls.append(
            {
                "source_entity_type": source_entity_type,
                "source_entity_id": source_entity_id,
                "owner_scope": owner_scope,
            }
        )
        return self._run_id

    async def status_for_run(self, *, run_id, owner_scope):
        self.status_calls.append(run_id)
        return self._status


class _CancelAgentService:
    def __init__(self):
        self.stop_calls: list[tuple[str, object]] = []

    async def stop_session(self, session_id: str, *, owner_scope):
        self.stop_calls.append((session_id, owner_scope))


def _task_service(*, session_service, agent_service=None, run_projection=None):
    return A2AServerService(
        agent_service=agent_service,
        session_service=session_service,
        skill_service=None,
        inference_model_service=None,
        policy_heads=None,
        breaker=None,
        run_projection=run_projection,
    )


_PRINCIPAL = Principal(user_id="owner-1", global_role=GlobalRole.USER)


@pytest.mark.asyncio
async def test_tasks_get_maps_run_status_to_a2a_state():
    run_id = uuid4()
    session = SimpleNamespace(id="session-1", active_execution_run_id=run_id)
    projection = _FakeRunProjection(run_id=run_id, status=RunStatus.WAITING)
    service = _task_service(
        session_service=_LookupSessionService(session),
        run_projection=projection,
    )

    response = await service.handle_task_get(
        {"id": "req-9", "params": {"id": "session-1"}},
        principal=_PRINCIPAL,
    )

    assert response["id"] == "req-9"
    result = response["result"]
    assert result["id"] == "session-1"
    assert result["kind"] == "task"
    # WAITING (approval/retry) surfaces to the caller as input-required.
    assert result["status"]["state"] == "input-required"
    assert projection.latest_calls[0]["source_entity_type"] == "session"
    assert projection.latest_calls[0]["source_entity_id"] == "session-1"


@pytest.mark.asyncio
async def test_tasks_get_submitted_when_no_run_admitted():
    session = SimpleNamespace(id="session-1", active_execution_run_id=None)
    service = _task_service(
        session_service=_LookupSessionService(session),
        run_projection=_FakeRunProjection(run_id=None, status=None),
    )

    response = await service.handle_task_get(
        {"id": "req-9", "params": {"id": "session-1"}},
        principal=_PRINCIPAL,
    )

    assert response["result"]["status"]["state"] == "submitted"


@pytest.mark.asyncio
async def test_tasks_get_unknown_task_returns_not_found_error():
    service = _task_service(
        session_service=_LookupSessionService(None),
        run_projection=_FakeRunProjection(),
    )

    response = await service.handle_task_get(
        {"id": "req-9", "params": {"id": "missing"}},
        principal=_PRINCIPAL,
    )

    assert "result" not in response
    assert response["error"]["code"] == -32001
    assert response["error"]["message"] == "Task not found"


@pytest.mark.asyncio
async def test_tasks_get_missing_params_id_returns_invalid_params():
    service = _task_service(session_service=_LookupSessionService(None))

    response = await service.handle_task_get(
        {"id": "req-9", "params": {}},
        principal=_PRINCIPAL,
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "params.id is required"


@pytest.mark.asyncio
async def test_tasks_cancel_reports_canceled_only_when_run_is_cancelled():
    run_id = uuid4()
    session = SimpleNamespace(id="session-1", active_execution_run_id=run_id)
    agent = _CancelAgentService()
    service = _task_service(
        session_service=_LookupSessionService(session),
        agent_service=agent,
        run_projection=_FakeRunProjection(run_id=run_id, status=RunStatus.CANCELLED),
    )

    response = await service.handle_task_cancel(
        {"id": "req-9", "params": {"id": "session-1"}},
        principal=_PRINCIPAL,
    )

    assert agent.stop_calls
    assert agent.stop_calls[0][0] == "session-1"
    assert response["result"]["status"]["state"] == "canceled"


@pytest.mark.asyncio
async def test_tasks_cancel_reports_observed_state_while_cancellation_is_in_flight():
    """CancelRun travels through the kernel; the response must not fake 'canceled'."""
    run_id = uuid4()
    session = SimpleNamespace(id="session-1", active_execution_run_id=run_id)
    agent = _CancelAgentService()
    service = _task_service(
        session_service=_LookupSessionService(session),
        agent_service=agent,
        run_projection=_FakeRunProjection(run_id=run_id, status=RunStatus.RUNNING),
    )

    response = await service.handle_task_cancel(
        {"id": "req-9", "params": {"id": "session-1"}},
        principal=_PRINCIPAL,
    )

    assert agent.stop_calls
    assert response["result"]["status"]["state"] == "working"


@pytest.mark.asyncio
async def test_tasks_cancel_unknown_task_returns_not_found_error():
    agent = _CancelAgentService()
    service = _task_service(
        session_service=_LookupSessionService(None),
        agent_service=agent,
    )

    response = await service.handle_task_cancel(
        {"id": "req-9", "params": {"id": "missing"}},
        principal=_PRINCIPAL,
    )

    assert not agent.stop_calls
    assert response["error"]["code"] == -32001
    assert response["error"]["message"] == "Task not found"
