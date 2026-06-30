#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.application.services.a2a_server_service import A2AServerService


@pytest.mark.asyncio
async def test_precheck_rejects_when_circuit_open():
    agent = MagicMock()
    session_service = MagicMock()
    skill_service = MagicMock()
    llm_service = MagicMock()
    llm_service.get_default_model = AsyncMock(return_value=MagicMock(id="model-1"))

    svc = A2AServerService(agent, session_service, skill_service, llm_service)
    breaker = MagicMock()
    breaker.is_open = AsyncMock(return_value=True)

    with patch("app.application.services.a2a_server_service.get_runtime_config") as cfg, patch.object(
        svc,
        "_breaker",
        breaker,
    ):
        cfg.return_value.feature_flags.enable_agent_features = True
        cfg.return_value.model_resilience.fast_fail_on_open_circuit = True
        guard = await svc._precheck_model()
    assert guard is not None
    assert "不可用" in guard["error"]["message"]
    session_service.create_session.assert_not_called()
