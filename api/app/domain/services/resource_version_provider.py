"""Provider contract and registry for independently versioned resources."""

from collections.abc import Iterable
from typing import Protocol

from app.domain.models.resource_bindings import (
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope


class ResourceVersionProvider(Protocol):
    kind: ResourceKind

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion:
        """Resolve an owned, published immutable version."""
        ...

    async def list_published_versions(
        self,
        resource_id: str,
        scope: OwnerScope,
    ) -> list[PublishedResourceVersion]:
        """Return owner-scoped upgrade targets, including the current pin."""
        ...


class ResourceVersionProviderNotRegisteredError(LookupError):
    pass


class ResourceVersionProviderRegistry:
    def __init__(
        self,
        providers: Iterable[ResourceVersionProvider] = (),
    ) -> None:
        self._providers: dict[
            ResourceKind,
            ResourceVersionProvider,
        ] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ResourceVersionProvider) -> None:
        try:
            kind = ResourceKind(provider.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("resource version provider must declare a valid kind") from exc
        if kind in self._providers:
            raise ValueError(f"resource version provider already registered for {kind.value}")
        self._providers[kind] = provider

    def get(self, kind: ResourceKind) -> ResourceVersionProvider:
        resolved_kind = ResourceKind(kind)
        provider = self._providers.get(resolved_kind)
        if provider is None:
            raise ResourceVersionProviderNotRegisteredError(
                f"no resource version provider registered for {resolved_kind.value}"
            )
        return provider
