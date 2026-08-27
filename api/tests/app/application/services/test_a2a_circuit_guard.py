from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.a2a_server_service import A2AServerService
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ExecutionPolicy
from tests.app.application_test_support import FakeCircuitBreaker


class PolicyHeads:
    async def active_execution(self, **_kwargs):
        return SimpleNamespace(revision=SimpleNamespace(policy=ExecutionPolicy()))


@pytest.mark.asyncio
async def test_precheck_rejects_when_circuit_open():
    agent = MagicMock()
    session_service = MagicMock()
    skill_service = MagicMock()
    inference_service = MagicMock()
    inference_service.resolve_chat = AsyncMock(return_value=MagicMock(id="model-1"))

    svc = A2AServerService(
        agent,
        session_service,
        skill_service,
        inference_service,
        PolicyHeads(),
        FakeCircuitBreaker(open_model_ids={"model-1"}),
    )

    guard = await svc._precheck_model(OwnerScope.personal("user-1"))
    assert guard is not None
    assert guard["error"]["code"] == -32001
    session_service.create_session.assert_not_called()
