"""Domain-facing embedding contract used by vector consumers."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.models.embedding_space import EmbeddingSpaceDescriptor
from app.domain.models.scope import OwnerScope


@runtime_checkable
class EmbeddingPort(Protocol):
    async def resolve_space(
        self, *, scope: OwnerScope | None, model_id: str | None = None
    ) -> EmbeddingSpaceDescriptor: ...

    async def embed(
        self,
        contents: Sequence[str],
        *,
        scope: OwnerScope | None,
        purpose_context: str = "unspecified",
        model_id: str | None = None,
        expected_space: EmbeddingSpaceDescriptor | None = None,
    ) -> list[list[float]]: ...
