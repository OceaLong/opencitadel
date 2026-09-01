"""One ownership/readiness validator for every resource-backed session."""

from dataclasses import dataclass

from app.domain.errors import BadRequestError
from app.domain.models.resource_bindings import (
    PublicationState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session_mode import SessionMode
from app.domain.services.resource_version_provider import (
    ResourceVersionProvider,
    ResourceVersionProviderNotRegisteredError,
    ResourceVersionProviderRegistry,
)


@dataclass(frozen=True)
class ValidatedSessionResources:
    """Resolved immutable resource versions, pinned at session creation."""

    mode: SessionMode
    knowledge_base: PublishedResourceVersion | None = None

    @property
    def versions(self) -> tuple[PublishedResourceVersion, ...]:
        return (self.knowledge_base,) if self.knowledge_base is not None else ()


class ResourceGuardService:
    """Validate requested session resources consistently across all APIs."""

    def __init__(self, *, providers: ResourceVersionProviderRegistry) -> None:
        self._providers = providers

    async def validate_session_request(
        self,
        *,
        mode: SessionMode,
        knowledge_base_id: str | None,
        knowledge_base_version_id: str | None,
        scope: OwnerScope,
    ) -> ValidatedSessionResources:
        knowledge_base = await self._resolve(
            ResourceKind.KNOWLEDGE_BASE,
            knowledge_base_id,
            knowledge_base_version_id,
            scope,
        )
        return ValidatedSessionResources(
            mode=mode,
            knowledge_base=knowledge_base,
        )

    async def _resolve(
        self,
        kind: ResourceKind,
        resource_id: str | None,
        version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion | None:
        if resource_id is None:
            if version_id is not None:
                raise BadRequestError("resource version was supplied without a resource")
            return None
        try:
            provider: ResourceVersionProvider = self._providers.get(kind)
        except ResourceVersionProviderNotRegisteredError as exc:
            raise BadRequestError(str(exc)) from exc
        resolved = await provider.resolve_published_version(resource_id, version_id, scope)
        if resolved.resource_kind != kind or resolved.resource_id != resource_id:
            raise BadRequestError("resource version provider returned a foreign resource")
        if version_id is not None and resolved.version_id != version_id:
            raise BadRequestError("resource version provider returned a foreign version")
        if not resolved.published or resolved.state not in {
            PublicationState.READY,
            PublicationState.DEGRADED,
        }:
            raise BadRequestError("resource version is not published")
        return resolved
