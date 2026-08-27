"""Scoped embedding facade for codebase chunks."""

from app.domain.models.scope import OwnerScope
from app.domain.vector_port import EmbeddingPort


class CodebaseVectorService:
    """Resolve embeddings through the inference control plane."""

    def __init__(
        self,
        embeddings: EmbeddingPort,
        *,
        scope: OwnerScope | None,
        enabled: bool,
        model_id: str | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._scope = scope
        self._enabled = enabled
        self._model_id = model_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def embed(self, content: str) -> list[float]:
        if not self.enabled or not content.strip():
            return []
        vectors = await self._embeddings.embed(
            [content],
            scope=self._scope,
            purpose_context="codebase.query",
            model_id=self._model_id,
        )
        return vectors[0] if vectors else []

    async def embed_batch(self, contents: list[str]) -> list[list[float]]:
        if not self.enabled:
            return [[] for _ in contents]
        return await self._embeddings.embed(
            contents,
            scope=self._scope,
            purpose_context="codebase.index",
            model_id=self._model_id,
        )
