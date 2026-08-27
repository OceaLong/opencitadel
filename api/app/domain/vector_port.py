"""Domain-facing embedding contract used by vector consumers."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.models.scope import OwnerScope


@runtime_checkable
class EmbeddingPort(Protocol):
    async def embed(
        self,
        contents: Sequence[str],
        *,
        scope: OwnerScope | None,
        purpose_context: str = "unspecified",
        model_id: str | None = None,
    ) -> list[list[float]]: ...
