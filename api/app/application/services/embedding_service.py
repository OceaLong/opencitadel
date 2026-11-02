from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Sequence
from hashlib import sha256

from app.application.ports.inference import EmbeddingFactoryPort
from app.application.ports.inference_dispatch import (
    close_inference_adapter,
    physical_request_context,
)
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_model_service import InferenceModelService
from app.domain.errors import BadRequestError, ServerRequestsError
from app.domain.models.embedding_space import (
    EmbeddingSpaceDescriptor,
    EmbeddingSpaceMismatch,
    validate_embedding,
)
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
    InferenceModelKind,
    InferencePurpose,
)
from app.domain.models.scope import OwnerScope

_EMBEDDING_CACHE_MAX_SIZE = 256
logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(
        self,
        bindings: InferenceBindingService,
        embedding_factory: EmbeddingFactoryPort,
        models: InferenceModelService | None = None,
        *,
        cache_max_size: int = _EMBEDDING_CACHE_MAX_SIZE,
        physical_dispatch=None,
    ) -> None:
        self._bindings = bindings
        self._physical_dispatch = physical_dispatch
        self._models = models
        self._embedding_factory = embedding_factory
        self._cache_max_size = cache_max_size
        self._cache: OrderedDict[tuple[str, str, str, str], list[float]] = OrderedDict()

    async def resolve_space(
        self, *, scope: OwnerScope | None, model_id: str | None = None
    ) -> EmbeddingSpaceDescriptor:
        return EmbeddingSpaceDescriptor.from_resolved(await self._resolve(scope, model_id))

    async def _resolve(self, scope, model_id):
        if model_id is None:
            return await self._bindings.resolve(InferencePurpose.EMBEDDING, scope=scope)
        if self._models is None:
            raise RuntimeError("model-specific embedding requires InferenceModelService")
        return await self._models.resolve_model(model_id, scope=scope)

    async def embed(
        self,
        contents: Sequence[str],
        *,
        scope: OwnerScope | None,
        purpose_context: str = "unspecified",
        model_id: str | None = None,
        expected_space: EmbeddingSpaceDescriptor | None = None,
    ) -> list[list[float]]:
        if not contents:
            return []
        if expected_space is not None:
            if model_id is not None and model_id != expected_space.model_id:
                logger.warning("embedding_space_mismatch: model_identity")
                raise EmbeddingSpaceMismatch()
            model_id = expected_space.model_id
        # Resolve on every call, before cache access, to recheck current access
        # and detect mutations under an unchanged database model ID.
        resolved = await self._resolve(scope, model_id)
        settings = resolved.model.settings
        if resolved.model.kind != InferenceModelKind.EMBEDDING or not isinstance(
            settings,
            EmbeddingModelSettings,
        ):
            raise BadRequestError(
                "Embedding 绑定未指向 Embedding 模型",
                error_key="inference.errors.bindingKindMismatch",
            )

        space = EmbeddingSpaceDescriptor.from_resolved(resolved)
        if expected_space is not None and space.digest != expected_space.digest:
            logger.warning("embedding_space_mismatch: descriptor_changed")
            raise EmbeddingSpaceMismatch()
        scope_key = f"{scope.type.value}:{scope.user_id}:{scope.team_id or ''}" if scope else None

        def cache_key(content):
            return (
                scope_key or "",
                space.digest,
                purpose_context,
                sha256(content.encode()).hexdigest(),
            )

        results: list[list[float] | None] = [None] * len(contents)
        missing_texts: list[str] = []
        missing_indices: list[int] = []
        for index, raw_content in enumerate(contents):
            content = str(raw_content)
            if not content.strip():
                results[index] = []
                continue
            key = cache_key(content)
            cached = self._cache.get(key) if scope_key is not None else None
            if cached is not None:
                self._cache.move_to_end(key)
                results[index] = list(cached)
                continue
            missing_texts.append(content)
            missing_indices.append(index)

        if missing_texts:
            try:
                adapter = self._embedding_factory.create_embedding(resolved)
            except ValueError as exc:
                raise BadRequestError(
                    "当前 Provider 不支持 Embedding",
                    error_key="inference.errors.unsupportedProviderKind",
                    error_params={"provider": resolved.provider.value, "kind": "embedding"},
                ) from exc
            try:
                with physical_request_context(
                    self._physical_dispatch, scope, purpose_context, resolved
                ):
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
                        for content, index, vector in zip(
                            batch, batch_indices, vectors, strict=True
                        ):
                            self._validate_vector(vector)
                            normalized = [float(value) for value in vector]
                            if scope_key is not None:
                                self._cache_set(cache_key(content), normalized)
                            results[index] = normalized
            finally:
                await close_inference_adapter(adapter)

        return [vector if vector is not None else [] for vector in results]

    def _cache_set(self, key: tuple[str, str, str, str], vector: list[float]) -> None:
        self._cache[key] = list(vector)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _validate_vector(vector: object) -> None:
        try:
            validate_embedding(vector)
        except ValueError as exc:
            raise ServerRequestsError(
                f"Embedding 向量必须为 {PLATFORM_EMBEDDING_DIMENSIONS} 维有限数值",
                error_key="inference.errors.embeddingInvalidVector",
                error_params={"dimensions": str(PLATFORM_EMBEDDING_DIMENSIONS)},
            ) from exc
