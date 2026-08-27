"""Application-facing inference capabilities."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from app.domain.external.llm import LLM
from app.domain.models.inference import (
    InferenceModelKind,
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.domain.runtime_policy import ModelResiliencePolicy

BreakerDecision = Literal["allow", "probe", "deny"]


class UnsupportedInferenceCombination(ValueError):
    def __init__(self, provider: InferenceProvider, kind: InferenceModelKind) -> None:
        self.provider = provider
        self.kind = kind
        super().__init__(f"{provider.value} does not support {kind.value} inference models")


@runtime_checkable
class EmbeddingClientPort(Protocol):
    async def embed_batch(self, contents: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class InferenceProviderCatalog(Protocol):
    def credential_required(self, provider: InferenceProvider) -> bool: ...

    def ensure_kind_supported(
        self,
        provider: InferenceProvider,
        kind: InferenceModelKind,
    ) -> None: ...


@runtime_checkable
class EmbeddingFactoryPort(Protocol):
    def create_embedding(self, model: ResolvedInferenceModel) -> EmbeddingClientPort: ...


@runtime_checkable
class ModelClientFactoryPort(Protocol):
    def create_model_client(
        self,
        model: ResolvedInferenceModel,
        *,
        thinking_enabled: bool = False,
    ) -> LLM: ...


@runtime_checkable
class CircuitBreakerPort(Protocol):
    async def is_open(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> bool: ...

    async def allow_request(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> BreakerDecision: ...

    async def record_success(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> None: ...

    async def record_failure(
        self,
        model_id: str,
        error: Exception,
        policy: ModelResiliencePolicy,
    ) -> None: ...

    async def snapshot(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> dict[str, object]: ...
