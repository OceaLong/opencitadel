from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from app.application.ports.inference import EmbeddingFactoryPort
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_model_service import InferenceModelService
from app.domain.errors import BadRequestError, ServerRequestsError
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
    InferenceModelKind,
    InferencePurpose,
)
from app.domain.models.scope import OwnerScope

_EMBEDDING_CACHE_MAX_SIZE = 256


class EmbeddingService:
    def __init__(
        self,
        bindings: InferenceBindingService,
        embedding_factory: EmbeddingFactoryPort,
        models: InferenceModelService | None = None,
        *,
        cache_max_size: int = _EMBEDDING_CACHE_MAX_SIZE,
    ) -> None:
        self._bindings = bindings
        self._models = models
        self._embedding_factory = embedding_factory
        self._cache_max_size = cache_max_size
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    async def embed(
        self,
        contents: Sequence[str],
        *,
        scope: OwnerScope | None,
        purpose_context: str = "unspecified",
        model_id: str | None = None,
    ) -> list[list[float]]:
        del purpose_context
        if not contents:
            return []
        if model_id is None:
            resolved = await self._bindings.resolve(InferencePurpose.EMBEDDING, scope=scope)
        else:
            if self._models is None:
                raise RuntimeError("model-specific embedding requires InferenceModelService")
            resolved = await self._models.resolve_model(model_id, scope=scope)
        settings = resolved.model.settings
        if resolved.model.kind != InferenceModelKind.EMBEDDING or not isinstance(
            settings,
            EmbeddingModelSettings,
        ):
            raise BadRequestError(
                "Embedding 绑定未指向 Embedding 模型",
                error_key="inference.errors.bindingKindMismatch",
            )

        try:
            adapter = self._embedding_factory.create_embedding(resolved)
        except ValueError as exc:
            raise BadRequestError(
                "当前 Provider 不支持 Embedding",
                error_key="inference.errors.unsupportedProviderKind",
                error_params={"provider": resolved.provider.value, "kind": "embedding"},
            ) from exc
        results: list[list[float] | None] = [None] * len(contents)
        missing_texts: list[str] = []
        missing_indices: list[int] = []
        for index, raw_content in enumerate(contents):
            content = str(raw_content)
            if not content.strip():
                results[index] = []
                continue
            key = (resolved.id, content)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                results[index] = list(cached)
                continue
            missing_texts.append(content)
            missing_indices.append(index)

        batch_size = settings.max_batch_size
        for start in range(0, len(missing_texts), batch_size):
            batch = missing_texts[start : start + batch_size]
            batch_indices = missing_indices[start : start + batch_size]
            vectors = await adapter.embed_batch(batch)
            if len(vectors) != len(batch):
                raise ServerRequestsError(
                    "Embedding 响应数量与请求数量不一致",
                    error_key="inference.errors.embeddingCardinalityMismatch",
                )
            for content, index, vector in zip(batch, batch_indices, vectors, strict=True):
                self._validate_vector(vector)
                normalized = [float(value) for value in vector]
                self._cache_set((resolved.id, content), normalized)
                results[index] = normalized

        return [vector if vector is not None else [] for vector in results]

    def _cache_set(self, key: tuple[str, str], vector: list[float]) -> None:
        self._cache[key] = list(vector)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _validate_vector(vector: object) -> None:
        if not isinstance(vector, list) or len(vector) != PLATFORM_EMBEDDING_DIMENSIONS:
            raise ServerRequestsError(
                f"Embedding 向量维度必须为 {PLATFORM_EMBEDDING_DIMENSIONS}",
                error_key="inference.errors.embeddingDimensionMismatch",
                error_params={"dimensions": str(PLATFORM_EMBEDDING_DIMENSIONS)},
            )
        if not all(isinstance(value, (int, float)) for value in vector):
            raise ServerRequestsError(
                "Embedding 向量包含非数值元素",
                error_key="inference.errors.embeddingInvalidVector",
            )
