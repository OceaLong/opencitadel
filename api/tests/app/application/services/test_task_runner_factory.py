#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from app.domain.errors import NotFoundError
from app.application.services.task_runner_factory import (
    CODE_ASK_SKILL_PROMPT,
    DOC_AGENT_SKILL_PROMPT,
    TaskRunnerFactory,
)
from app.domain.models.app_config import AgentConfig, AppConfig, MCPConfig, A2AConfig
from app.domain.models.codebase import Codebase, SessionMode
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.llm_model import LLMModel
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.models.resource_governance import (
    ResourceBindingProjection,
    ResourceKind,
)
from app.domain.models.skill import Skill


class _FakeCodebaseRepo:
    def __init__(self, codebase: Codebase | None):
        self._codebase = codebase

    async def get_by_id(self, codebase_id: str, scope=None):
        return self._codebase


class _ScopeGuardedCodebaseRepo:
    def __init__(self, unscoped_codebase: Codebase):
        self._unscoped_codebase = unscoped_codebase

    async def get_by_id(self, codebase_id: str, scope=None):
        if scope is None:
            return self._unscoped_codebase
        return None


class _EmptyKnowledgeBaseRepo:
    async def get_kb(self, kb_id: str, scope=None):
        return None


class _ScopeGuardedKnowledgeBaseRepo:
    def __init__(self, unscoped_kb: KnowledgeBase):
        self._unscoped_kb = unscoped_kb

    async def get_kb(self, kb_id: str, scope=None):
        if scope is None:
            return self._unscoped_kb
        return None


class _FakeCodebaseVersionRepo:
    def __init__(self, version: CodebaseVersion | None = None):
        self._version = version

    async def get_version(self, version_id: str, *, codebase_id: str | None = None):
        if (
            self._version is not None
            and self._version.id == version_id
            and self._version.codebase_id == codebase_id
        ):
            return self._version
        return None


class _FakeUow:
    def __init__(
        self,
        codebase: Codebase | None,
        codebase_version: CodebaseVersion | None = None,
    ):
        self.codebase = _FakeCodebaseRepo(codebase)
        self.codebase_version = _FakeCodebaseVersionRepo(codebase_version)
        self.knowledge_base = _EmptyKnowledgeBaseRepo()

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
async def test_ask_mode_codebase_tool_uses_published_snapshot_reader():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
        active_version_id="cbv1",
    )
    codebase_version = CodebaseVersion(
        id="cbv1",
        codebase_id="cb1",
        state=CodebaseVersionState.READY,
        published_at=datetime.now(timezone.utc),
        source_snapshot_key="snapshots/cbv1.tgz",
        source_digest="digest-cbv1",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    ingestion_sandbox = MagicMock(name="ingestion_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=ingestion_sandbox)

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase, codebase_version)
    factory._object_storage = MagicMock()
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))

    session = Session(
        id="sess-1",
        mode=SessionMode.ASK,
        model_id="model-1",
        owner_user_id="user-1",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-cbv1",
                resource_kind=ResourceKind.CODEBASE,
                resource_id="cb1",
                version_id="cbv1",
            )
        ],
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
    assert kwargs["workspace_path"] == "/home/ubuntu/codebase"
    assert kwargs["version_id"] == "cbv1"
    assert kwargs["source_reader"] is not None


@pytest.mark.anyio
async def test_agent_mode_codebase_tool_uses_session_sandbox():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
        active_version_id="cbv1",
    )
    codebase_version = CodebaseVersion(
        id="cbv1",
        codebase_id="cb1",
        state=CodebaseVersionState.READY,
        published_at=datetime.now(timezone.utc),
        source_snapshot_key="snapshots/cbv1.tgz",
        source_digest="digest-cbv1",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    ingestion_sandbox = MagicMock(name="ingestion_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=ingestion_sandbox)

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase, codebase_version)
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))
    factory._codebase_service = MagicMock()
    factory._object_storage = MagicMock()

    session = Session(
        id="sess-1",
        mode=SessionMode.AGENT,
        model_id="model-1",
        owner_user_id="user-1",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-cbv1",
                resource_kind=ResourceKind.CODEBASE,
                resource_id="cb1",
                version_id="cbv1",
            )
        ],
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
    assert kwargs["base_version_id"] == "cbv1"


@pytest.mark.anyio
async def test_ask_mode_injects_codebase_skill_prompt():
    codebase = Codebase(
        id="cb1",
        name="demo-project",
        sandbox_id="ingest-sb-1",
        workspace_path="/home/ubuntu/codebase",
        active_version_id="cbv1",
    )
    codebase_version = CodebaseVersion(
        id="cbv1",
        codebase_id="cb1",
        state=CodebaseVersionState.READY,
        published_at=datetime.now(timezone.utc),
        source_snapshot_key="snapshots/cbv1.tgz",
        source_digest="digest-cbv1",
    )
    session_sandbox = MagicMock(name="session_sandbox")
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=MagicMock())

    factory = _build_factory(sandbox_cls)
    factory._uow_factory = lambda: _FakeUow(codebase, codebase_version)
    factory._object_storage = MagicMock()
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))

    session = Session(
        id="sess-1",
        mode=SessionMode.ASK,
        model_id="model-1",
        owner_user_id="user-1",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-cbv1",
                resource_kind=ResourceKind.CODEBASE,
                resource_id="cb1",
                version_id="cbv1",
            )
        ],
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


@pytest.mark.anyio
async def test_kb_only_agent_mode_injects_document_agent_prompt():
    """Catches KB-only Agent sessions missing the document-agent operating prompt."""
    knowledge_base = KnowledgeBase(id="kb-1", name="company-handbook")
    uow = _FakeUow(None)
    uow.knowledge_base.get_kb = AsyncMock(return_value=knowledge_base)
    uow.knowledge_version = MagicMock()
    uow.knowledge_version.get_version = AsyncMock(
        return_value=KnowledgeBaseVersion(
            id="kbv1",
            knowledge_base_id="kb-1",
            state=KnowledgeVersionState.READY,
            published_at=datetime.now(timezone.utc),
        )
    )
    factory = _build_factory(MagicMock())
    factory._uow_factory = lambda: uow
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))
    session = Session(
        id="sess-1",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-kbv1",
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id="kb-1",
                version_id="kbv1",
            )
        ],
        mode=SessionMode.AGENT,
        model_id="model-1",
        owner_user_id="user-1",
    )
    llm = MagicMock(supports_multimodal=False)
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
        "app.application.services.task_runner_factory.KnowledgeBaseTool",
        return_value=MagicMock(),
    ) as mock_knowledge_base_tool, patch(
        "app.application.services.task_runner_factory.LazySandbox",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.AgentTaskRunner",
        side_effect=capture_runner,
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        await factory.create_runner(session)

    assert DOC_AGENT_SKILL_PROMPT in captured["skill_prompt"]
    _, tool_kwargs = mock_knowledge_base_tool.call_args
    assert tool_kwargs["kb_id"] == "kb-1"
    assert tool_kwargs["version_id"] == "kbv1"


@pytest.mark.anyio
async def test_create_runner_rejects_persisted_cross_scope_codebase():
    victim_codebase = Codebase(
        id="victim-cb",
        name="victim-project",
        owner_user_id="victim-user",
    )
    uow = _FakeUow(None)
    uow.codebase = _ScopeGuardedCodebaseRepo(victim_codebase)
    factory = _build_factory(MagicMock())
    factory._uow_factory = lambda: uow
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))
    session = Session(
        id="sess-1",
        mode=SessionMode.ASK,
        model_id="model-1",
        owner_user_id="attacker-user",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-victim-cb",
                resource_kind=ResourceKind.CODEBASE,
                resource_id="victim-cb",
                version_id="victim-cbv1",
            )
        ],
    )
    llm = MagicMock(supports_multimodal=False)

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
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        with pytest.raises(NotFoundError, match="代码库"):
            await factory.create_runner(session)


@pytest.mark.anyio
async def test_create_runner_rejects_persisted_cross_scope_knowledge_base():
    victim_kb = KnowledgeBase(
        id="victim-kb",
        name="victim-knowledge-base",
        owner_user_id="victim-user",
    )
    uow = _FakeUow(None)
    uow.knowledge_base = _ScopeGuardedKnowledgeBaseRepo(victim_kb)
    factory = _build_factory(MagicMock())
    factory._uow_factory = lambda: uow
    factory._memory_service.save_from_tool = AsyncMock(return_value=MagicMock(id="mem-1"))
    session = Session(
        id="sess-1",
        mode=SessionMode.ASK,
        model_id="model-1",
        owner_user_id="attacker-user",
        resource_bindings=[
            ResourceBindingProjection(
                binding_id="binding-victim-kb",
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id="victim-kb",
                version_id="victim-kbv1",
            )
        ],
    )
    llm = MagicMock(supports_multimodal=False)

    with patch.object(
            factory,
            "_resolve_llm_and_config",
            AsyncMock(return_value=(llm, AgentConfig(), None, "", "", _llm_model())),
    ), patch(
        "app.application.services.task_runner_factory.build_subagent_tool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.KnowledgeBaseTool",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.LazySandbox",
        return_value=MagicMock(),
    ), patch(
        "app.application.services.task_runner_factory.get_runtime_config",
        return_value=_runtime_config(),
    ):
        with pytest.raises(NotFoundError, match="知识库"):
            await factory.create_runner(session)


@pytest.mark.anyio
async def test_runtime_config_preserves_team_scope():
    factory = _build_factory(MagicMock())
    session = Session(
        id="sess-1",
        owner_user_id="user-1",
        team_id="team-1",
    )

    await factory._refresh_runtime_config(session)

    factory._config_provider.resolve_for_owner.assert_awaited_once_with(
        OwnerScope.team("user-1", "team-1"),
    )


@pytest.mark.anyio
async def test_llm_resolution_preserves_team_scope():
    factory = _build_factory(MagicMock())
    model = _llm_model()
    factory._llm_model_service.resolve_model = AsyncMock(return_value=model)
    factory._memory_service.recall_for_session = AsyncMock(return_value="")
    session = Session(
        id="sess-1",
        model_id=model.id,
        owner_user_id="user-1",
        team_id="team-1",
    )

    with patch.object(
        factory,
        "_maybe_auto_recommend_skill",
        AsyncMock(),
    ), patch(
        "app.application.services.task_runner_factory.create_resilient_llm",
        return_value=MagicMock(),
    ):
        await factory._resolve_llm_and_config(session)

    factory._llm_model_service.resolve_model.assert_awaited_once_with(
        model.id,
        scope=OwnerScope.team("user-1", "team-1"),
    )


@pytest.mark.anyio
async def test_skill_resolution_preserves_team_scope():
    factory = _build_factory(MagicMock())
    model = _llm_model()
    skill = Skill(id="skill-1", name="Scoped Skill", slug="scoped-skill")
    factory._skill_service.get_skill = AsyncMock(return_value=skill)
    factory._llm_model_service.resolve_model = AsyncMock(return_value=model)
    factory._memory_service.recall_for_session = AsyncMock(return_value="")
    session = Session(
        id="sess-1",
        skill_id=skill.id,
        model_id=model.id,
        owner_user_id="user-1",
        team_id="team-1",
    )

    with patch(
        "app.application.services.task_runner_factory.create_resilient_llm",
        return_value=MagicMock(),
    ):
        await factory._resolve_llm_and_config(session)

    factory._skill_service.get_skill.assert_awaited_once_with(
        skill.id,
        scope=OwnerScope.team("user-1", "team-1"),
    )
