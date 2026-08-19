import pytest

from app.domain.errors import BadRequestError
from app.domain.models.codebase import SessionMode
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.resource_version_provider import ResourceVersionProviderRegistry


class _Provider:
    def __init__(self, kind, version):
        self.kind = kind
        self.version = version
        self.calls = []

    async def resolve_published_version(self, resource_id, version_id, scope):
        self.calls.append((resource_id, version_id, scope))
        return self.version


@pytest.mark.asyncio
async def test_guard_resolves_only_matching_published_versions():
    """Catches generic creation binding a foreign or unpublished resource."""
    from app.application.services.resource_guard_service import ResourceGuardService

    scope = OwnerScope.personal("u1")
    provider = _Provider(
        ResourceKind.KNOWLEDGE_BASE,
        PublishedResourceVersion(
            ResourceKind.KNOWLEDGE_BASE,
            "kb1",
            "v2",
            state=BuildState.SUCCEEDED,
            published=True,
        ),
    )
    guard = ResourceGuardService(
        providers=ResourceVersionProviderRegistry([provider]),
    )

    validated = await guard.validate_session_request(
        mode=SessionMode.AGENT,
        codebase_id=None,
        codebase_version_id=None,
        knowledge_base_id="kb1",
        knowledge_base_version_id="v2",
        scope=scope,
    )

    assert validated.mode is SessionMode.AGENT
    assert validated.knowledge_base.version_id == "v2"
    assert provider.calls == [("kb1", "v2", scope)]


@pytest.mark.asyncio
async def test_guard_rejects_unpublished_resources_with_the_same_error():
    """Catches specialized entry points accepting a building resource."""
    from app.application.services.resource_guard_service import ResourceGuardService

    provider = _Provider(
        ResourceKind.CODEBASE,
        PublishedResourceVersion(
            ResourceKind.CODEBASE,
            "cb1",
            "v1",
            state=BuildState.RUNNING,
            published=False,
        ),
    )
    guard = ResourceGuardService(
        providers=ResourceVersionProviderRegistry([provider]),
    )

    with pytest.raises(BadRequestError, match="resource version is not published"):
        await guard.validate_session_request(
            mode=SessionMode.ASK,
            codebase_id="cb1",
            codebase_version_id=None,
            knowledge_base_id=None,
            knowledge_base_version_id=None,
            scope=OwnerScope.personal("u1"),
        )
