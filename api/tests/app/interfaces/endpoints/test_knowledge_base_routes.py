"""Knowledge-base session route regressions."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.application.dto.knowledge_build import (
    KnowledgeBuildProjection,
    KnowledgeVersionHistoryProjection,
    KnowledgeVersionProjection,
)
from app.domain.models.codebase import SessionMode
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.resource_governance import BuildState
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints import knowledge_base_routes
from app.interfaces.schemas.knowledge_base import CreateKnowledgeBaseSessionRequest


@pytest.mark.asyncio
async def test_kb_session_route_preserves_explicit_agent_mode():
    """Catches the route/service coercing a requested KB Agent session to Ask."""
    ctx = WorkspaceContext(
        principal=Principal(user_id="u1"), scope=OwnerScope.personal("u1"),
    )

    class Service:
        async def create_session_for_kb(self, kb_id, **kwargs):
            assert kb_id == "kb1"
            assert kwargs["mode"] is SessionMode.AGENT
            return SimpleNamespace(id="s1", mode=SessionMode.AGENT)

    response = await knowledge_base_routes.create_kb_session(
        "kb1", CreateKnowledgeBaseSessionRequest(mode=SessionMode.AGENT),
        ctx, Principal(user_id="u1"), Service(),
    )
    assert response.data.mode is SessionMode.AGENT


@pytest.mark.asyncio
async def test_version_build_routes_forward_exact_owner_scoped_identities():
    ctx = WorkspaceContext(
        principal=Principal(user_id="u1"),
        scope=OwnerScope.personal("u1"),
    )
    calls = []
    build = KnowledgeBuildProjection(
        id="b1",
        knowledge_base_id="kb1",
        version_id="candidate",
        parent_version_id="v1",
        command_key="retry-key",
        state=BuildState.QUEUED,
        created_at=datetime.now(timezone.utc),
        can_cancel=True,
    )
    version = KnowledgeVersionProjection(
        id="candidate",
        knowledge_base_id="kb1",
        parent_version_id="v1",
        build_id="b1",
        state=KnowledgeVersionState.BUILDING,
        created_at=datetime.now(timezone.utc),
        is_candidate=True,
        build=build,
    )

    class Service:
        async def list_versions(self, kb_id, **kwargs):
            calls.append(("list", kb_id, kwargs["scope"]))
            return KnowledgeVersionHistoryProjection(
                knowledge_base_id=kb_id,
                active_version_id="v1",
                active_build=build,
                versions=[version],
            )

        async def create_build(self, kb_id, **kwargs):
            calls.append(("create", kb_id, kwargs["scope"]))
            return version

        async def get_version(self, kb_id, version_id, **kwargs):
            calls.append(
                ("get", kb_id, version_id, kwargs["scope"])
            )
            return version

        async def retry_build(self, kb_id, build_id, **kwargs):
            calls.append(
                ("retry", kb_id, build_id, kwargs["scope"])
            )
            return version

        async def cancel_build(self, kb_id, build_id, **kwargs):
            calls.append(
                ("cancel", kb_id, build_id, kwargs["scope"])
            )
            return build

    service = Service()
    await knowledge_base_routes.list_kb_versions("kb1", ctx, service)
    await knowledge_base_routes.get_kb_version(
        "kb1", "candidate", ctx, service
    )
    await knowledge_base_routes.create_kb_build(
        "kb1", ctx, Principal(user_id="u1"), service
    )
    await knowledge_base_routes.retry_kb_build(
        "kb1", "b1", ctx, Principal(user_id="u1"), service
    )
    await knowledge_base_routes.cancel_kb_build(
        "kb1", "b1", ctx, Principal(user_id="u1"), service
    )

    assert calls == [
        ("list", "kb1", ctx.scope),
        ("get", "kb1", "candidate", ctx.scope),
        ("create", "kb1", ctx.scope),
        ("retry", "kb1", "b1", ctx.scope),
        ("cancel", "kb1", "b1", ctx.scope),
    ]
