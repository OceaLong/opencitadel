"""Scoped embedding facade for knowledge-base chunks."""

from app.domain.models.embedding_space import (
    EmbeddingSpaceDescriptor,
    EmbeddingSpaceMismatch,
    validate_embedding,
)
from app.domain.models.scope import OwnerScope
from app.domain.vector_port import EmbeddingPort


class KBVectorService:
    def __init__(
        self,
        embeddings: EmbeddingPort,
        *,
        scope: OwnerScope | None,
        enabled: bool,
        model_id: str | None = None,
        expected_space: EmbeddingSpaceDescriptor | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._scope = scope
        self.enabled = enabled
        self._model_id = model_id
        self._expected_space = expected_space

    async def embed(
        self, content: str, *, expected_space: EmbeddingSpaceDescriptor | None = None
    ) -> list[float]:
        if not self.enabled or not content.strip():
            return []
        space = expected_space or self._expected_space
        if space is None:
            raise EmbeddingSpaceMismatch("published index has no verified local space descriptor")
        vectors = await self._embeddings.embed(
            [content],
            scope=self._scope,
            purpose_context="knowledge_base.query",
            model_id=space.model_id,
            expected_space=space,
        )
        return vectors[0] if vectors else []

    async def embed_batch(self, contents: list[str]) -> list[list[float]]:
        if not self.enabled:
            return [[] for _ in contents]
        if self._expected_space is None:
            raise EmbeddingSpaceMismatch("index build has no frozen embedding space")
        vectors = await self._embeddings.embed(
            contents,
            scope=self._scope,
            purpose_context="knowledge_base.index",
            model_id=self._model_id,
            expected_space=self._expected_space,
        )
        if len(vectors) != len(contents):
            raise ValueError("embedding cardinality mismatch")
        for vector in vectors:
            validate_embedding(vector, self._expected_space.dimensions)
        return vectors
