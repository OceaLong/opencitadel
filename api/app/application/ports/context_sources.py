"""Formal private source boundaries; public chat bodies are never authority."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.execution.context_models import AcceptedContextCut, FormalContextSource


@dataclass(frozen=True)
class ContextSourcePage:
    sources: tuple[FormalContextSource, ...]
    next_cursor: str | None


class ContextSourcesPort(Protocol):
    async def legacy_conversation(self, scope, collection_id: str) -> list[dict[str, str]]:
        """Only the legacy definition may retain its original public-history input."""
        ...

    async def freeze(
        self, scope, collection_id: str, *, request_id: UUID | None = None
    ) -> AcceptedContextCut: ...
    async def page(
        self, cut: AcceptedContextCut, *, cursor: str | None = None, limit: int = 200
    ) -> ContextSourcePage: ...
