"""Skill declarations are hard authorization boundaries for model tools."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from app.application.execution.agent_tool_catalog import AgentToolCatalog, _SessionSandbox
from app.domain.execution.activity import ActivityContext
from app.domain.models.inference import (
    ChatModelSettings,
    InferenceCapabilities,
    InferenceEndpoint,
    InferenceModel,
    ResolvedInferenceModel,
)
from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime
from app.domain.models.skill import Skill
from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY
from tests.app.execution_test_support import run_execution_context_for

CONTEXT = ActivityContext(
    worker_id="worker-1",
    claim_generation=1,
    idempotency_key="activity-1",
    owner_user_id="user-1",
    team_id=None,
    run=run_execution_context_for("agent"),
)


class _Skills:
    def __init__(self, skill: Skill | None) -> None:
        self.skill = skill

    async def get_by_id(self, skill_id, scope=None):
        assert scope.user_id == "user-1"
        if self.skill is not None:
            assert self.skill.id == skill_id
        return self.skill


class _Uow(AbstractAsyncContextManager):
    def __init__(self, skill: Skill | None) -> None:
        self.skill = _Skills(skill)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _MCPManager:
    connection_errors: ClassVar[dict] = {}

    async def get_all_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "mcp_collector_read",
                    "description": "read",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def get_tool_policy(self, name):
        assert name == "mcp_collector_read"
        return CONSERVATIVE_TOOL_POLICY


class _MCPPool:
    async def acquire(self, config, *, policy):
        assert policy == CONTEXT.run.policy_snapshot.common.activity
        return _MCPManager()


class _A2AManager:
    agent_cards: ClassVar[dict] = {}


class _A2APool:
    async def acquire(self, config, *, policy):
        assert policy == CONTEXT.run.policy_snapshot.common.activity
        return _A2AManager()


class _MCPServers:
    def __init__(self) -> None:
        self.refs = None

    async def resolve_mcp_runtime(self, scope, *, server_refs=None):
        self.refs = server_refs
        return MCPRuntime()


class _A2AServers:
    def __init__(self) -> None:
        self.refs = None

    async def resolve_a2a_runtime(self, scope, *, server_refs=None):
        self.refs = server_refs
        return A2ARuntime()


class _Models:
    def __init__(self, model: ResolvedInferenceModel | None = None) -> None:
        self.model = model or _resolved_model()

    async def resolve_chat(self, model_id=None, *, scope):
        assert scope.user_id == "user-1"
        return self.model


def _resolved_model(*, image_generation: bool = False) -> ResolvedInferenceModel:
    return ResolvedInferenceModel(
        model=InferenceModel(
            id="model-1",
            endpoint_id="endpoint-1",
            display_name="model",
            model_name="provider-model",
            settings=ChatModelSettings(),
            capabilities=InferenceCapabilities(image_generation=image_generation),
        ),
        endpoint=InferenceEndpoint(
            id="endpoint-1",
            display_name="endpoint",
            credential="secret",
        ),
    )


class _ImageGenerator:
    async def generate(self, *_args, **_kwargs):
        return "https://example.com/generated.png"


def _catalog(
    skill: Skill | None,
    *,
    model: ResolvedInferenceModel | None = None,
    image_generator=None,
    llm_factory=None,
):
    mcp_servers = _MCPServers()
    a2a_servers = _A2AServers()
    catalog = AgentToolCatalog(
        uow_factory=lambda: _Uow(skill),
        sandbox_factory=object,
        search_engine=object(),
        mcp_connection_pool=_MCPPool(),
        a2a_connection_pool=_A2APool(),
        mcp_servers=mcp_servers,
        a2a_servers=a2a_servers,
        object_storage=object(),
        file_storage=object(),
        models=_Models(model),
        image_generator=image_generator or _ImageGenerator(),
        codebases=object(),
        artifacts=object(),
        memories=object(),
        llm_factory=llm_factory,
    )
    return catalog, mcp_servers, a2a_servers


def _payload(skill_id: str):
    return {
        "session_id": "session-1",
        "mode": "agent",
        "skill_id": skill_id,
        "resource_bindings": [],
    }


@pytest.mark.asyncio
async def test_skill_filters_tool_exposure_and_execution() -> None:
    skill = Skill(
        id="skill-1",
        name="restricted",
        slug="restricted",
        allowed_tools=["search_web"],
        mcp_server_refs=["collector"],
        a2a_server_refs=["agent-1"],
    )
    catalog, mcp_servers, a2a_servers = _catalog(skill)

    definitions = await catalog.definitions(_payload(skill.id), CONTEXT)

    assert [item.name for item in definitions] == ["search_web"]
    assert mcp_servers.refs == ("collector",)
    assert a2a_servers.refs == ("agent-1",)
    with pytest.raises(ValueError, match="absent"):
        await catalog.invoke(
            _payload(skill.id),
            CONTEXT,
            name="mcp_collector_read",
            arguments={},
        )


@pytest.mark.asyncio
async def test_disabled_skill_is_rejected_before_tools_are_built() -> None:
    skill = Skill(
        id="skill-1",
        name="disabled",
        slug="disabled",
        enabled=False,
        allowed_tools=["search_web"],
    )
    catalog, _, _ = _catalog(skill)

    with pytest.raises(ValueError, match="disabled"):
        await catalog.definitions(_payload(skill.id), CONTEXT)


@pytest.mark.asyncio
async def test_image_generation_is_a_governed_formal_tool() -> None:
    skill = Skill(
        id="skill-1",
        name="image generation",
        slug="image-generation",
        allowed_tools=["generate_image"],
    )
    model = _resolved_model(image_generation=True)
    catalog, _, _ = _catalog(skill, model=model)

    definitions = await catalog.definitions(_payload(skill.id), CONTEXT)

    assert [item.name for item in definitions] == ["generate_image"]
    assert definitions[0].requires_approval is True
    result = await catalog.invoke(
        _payload(skill.id),
        CONTEXT,
        name="generate_image",
        arguments={"prompt": "draw a citadel"},
    )
    assert result["success"] is True
    assert result["data"]["image_url"] == "https://example.com/generated.png"


@pytest.mark.asyncio
async def test_session_sandbox_allocation_passes_verified_owner_scope() -> None:
    session = SimpleNamespace(sandbox_id=None)
    repository = SimpleNamespace(
        lock_by_id=AsyncMock(return_value=session),
        save=AsyncMock(),
    )

    class _SessionUow(AbstractAsyncContextManager):
        def __init__(self):
            self.session = repository

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def commit(self):
            return None

    created = SimpleNamespace(id="sandbox-1")
    factory = SimpleNamespace(
        get=AsyncMock(return_value=None),
        create=AsyncMock(return_value=created),
    )
    scope = CONTEXT.run.owner_scope
    sandbox = _SessionSandbox(
        session_id="session-1",
        scope=scope,
        sandbox_factory=factory,
        uow_factory=_SessionUow,
        on_ready=AsyncMock(),
    )

    assert await sandbox._resolve() is created
    factory.create.assert_awaited_once_with(owner_scope=scope)
    repository.save.assert_awaited_once_with(session)
    assert session.sandbox_id == "sandbox-1"


@pytest.mark.asyncio
async def test_rerank_llm_is_wired_when_kb_bound() -> None:
    # Regression: rerank stayed a no-op because the catalog never built a chat
    # client for the KnowledgeBaseTool. With a bound KB and rerank enabled the
    # llm_factory must be invoked so retrieval can actually rerank.
    skill = Skill(id="skill-1", name="kb", slug="kb", allowed_tools=["kb_search"])
    calls: list[dict] = []

    def _factory(model, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(invoke=AsyncMock())

    catalog, _, _ = _catalog(skill, llm_factory=_factory)
    payload = {
        "session_id": "session-1",
        "mode": "agent",
        "skill_id": "skill-1",
        "resource_bindings": [
            {
                "resource_kind": "knowledge_base",
                "resource_id": "kb-1",
                "version_id": "v1",
            }
        ],
    }

    await catalog.definitions(payload, CONTEXT)

    assert len(calls) == 1
    assert calls[0]["thinking_enabled"] is False
