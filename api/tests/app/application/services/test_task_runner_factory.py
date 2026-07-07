#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services.task_runner_factory import (
    CODE_ASK_SKILL_PROMPT,
    TaskRunnerFactory,
)
from app.domain.models.app_config import AgentConfig, AppConfig, MCPConfig, A2AConfig
from app.domain.models.codebase import Codebase, SessionMode
from app.domain.models.llm_model import LLMModel
from app.domain.models.session import Session


class _FakeCodebaseRepo:
    def __init__(self, codebase: Codebase | None):
        self._codebase = codebase

    async def get_by_id(self, codebase_id: str):
        return self._codebase


class _FakeUow:
    def __init__(self, codebase: Codebase | None):
        self.codebase = _FakeCodebaseRepo(codebase)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    @property
    def session(self):
        repo = MagicMock()
        repo.list_events = AsyncMock(return_value=[])
        return repo


def _build_factory(sandbox_cls: MagicMock) -> TaskRunnerFactory:
    config_provider = MagicMock()
    config_provider.resolve_for_owner = AsyncMock(
        return_value=AppConfig(
            agent_config=AgentConfig(),
            mcp_config=MCPConfig(),
            a2a_config=A2AConfig(),
        ),
    )
    return TaskRunnerFactory(
        uow_factory=lambda: _FakeUow(None),
        llm_model_service=MagicMock(),
        skill_service=MagicMock(),
        memory_service=MagicMock(),
        sandbox_cls=sandbox_cls,
        json_parser=MagicMock(),
        search_engine=MagicMock(),
        file_storage=MagicMock(),
        config_provider=config_provider,
        checkpoint_service=MagicMock(),
        task_state_port=MagicMock(),
        observability_port=MagicMock(),
        event_sequence_port=MagicMock(),
        session_state_factory=lambda: MagicMock(),
        mcp_connection_pool=MagicMock(),
        a2a_connection_pool=MagicMock(),
    )


def _llm_model() -> LLMModel:
    return LLMModel(
        id="model-1",
        name="test-model",
        provider="openai",
        model="gpt-test",
        endpoint_id="ep-1",
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _runtime_config():
    return AppConfig()


@pytest.mark.anyio
async def test_ask_mode_codebase_tool_uses_ingestion_sandbox():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    ingestion_sandbox = MagicMock(name="ingestion_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=ingestion_sandbox)

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase)
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))

    session = Session(
        id="sess-1",
        codebase_id="cb1",
        mode=SessionMode.ASK,
        model_id="model-1",
    )

    llm = MagicMock()
    llm.supports_multimodal = False

    with patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), None, "", "", _llm_model())),
    ), patch(
        "app.application.services.task_runner_factory.build_subagent_tool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.CodebaseTool",
    ) as mock_codebase_tool, patch(
        "app.application.services.task_runner_factory.LazySandbox",
        return_value=session_sandbox,
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        await factory.create_runner(session)

    mock_codebase_tool.assert_called_once()
    _, kwargs = mock_codebase_tool.call_args
    assert kwargs["sandbox"] is ingestion_sandbox
    assert kwargs["workspace_path"] == "/home/ubuntu/codebase"


@pytest.mark.anyio
async def test_agent_mode_codebase_tool_uses_session_sandbox():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    ingestion_sandbox = MagicMock(name="ingestion_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=ingestion_sandbox)

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase)
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))
    factory._codebase_service = MagicMock()
    factory._object_storage = MagicMock()

    session = Session(
        id="sess-1",
        codebase_id="cb1",
        mode=SessionMode.AGENT,
        model_id="model-1",
    )

    llm = MagicMock()
    llm.supports_multimodal = False

    with patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), None, "", "", _llm_model())),
    ), patch(
        "app.application.services.task_runner_factory.build_subagent_tool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.CodebaseTool",
    ) as mock_codebase_tool, patch(
        "app.application.services.task_runner_factory.LazySandbox",
        return_value=session_sandbox,
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        await factory.create_runner(session)

    mock_codebase_tool.assert_called_once()
    _, kwargs = mock_codebase_tool.call_args
    assert kwargs["sandbox"] is session_sandbox


@pytest.mark.anyio
async def test_ask_mode_injects_codebase_skill_prompt():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=MagicMock())

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase)
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))

    session = Session(
        id="sess-1",
        codebase_id="cb1",
        mode=SessionMode.ASK,
        model_id="model-1",
    )

    llm = MagicMock()
    llm.supports_multimodal = False
    captured = {}

    def capture_runner(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), None, "", "", _llm_model())),
    ), patch(
        "app.application.services.task_runner_factory.build_subagent_tool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.CodebaseTool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.LazySandbox",
        return_value=session_sandbox,
    ), patch(
        "app.application.services.task_runner_factory.AgentTaskRunner",
        side_effect=capture_runner,
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        await factory.create_runner(session)

    expected_prompt = CODE_ASK_SKILL_PROMPT.format(
        name="demo-project",
        workspace_path="/home/ubuntu/codebase",
    )
    assert expected_prompt in captured["skill_prompt"]
