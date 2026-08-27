"""Infrastructure implementations of application inference capabilities."""

from __future__ import annotations

from app.application.ports.crypto import OutboundNetworkPolicy
from app.application.ports.inference import (
    EmbeddingClientPort,
    EmbeddingFactoryPort,
    InferenceProviderCatalog,
    ModelClientFactoryPort,
    UnsupportedInferenceCombination,
)
from app.application.ports.observability import ModelMetricsPort, ModelMetricsSnapshot
from app.domain.external.llm import LLM
from app.domain.models.inference import (
    InferenceModelKind,
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ModelResiliencePolicy
from app.infrastructure.external.inference.registry import provider_spec
from app.infrastructure.external.llm.resilient_llm import create_resilient_llm
from app.infrastructure.observability.llm_metrics import (
    get_llm_metrics_snapshot,
    get_resilience_metrics_snapshot,
    record_llm_resilience_event,
)
from app.infrastructure.security.outbound_http import DEFAULT_OUTBOUND_NETWORK_POLICY


class InfrastructureInferenceProviderAdapter(
    InferenceProviderCatalog,
    EmbeddingFactoryPort,
    ModelClientFactoryPort,
):
    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        self._outbound_policy = outbound_policy

    def credential_required(self, provider: InferenceProvider) -> bool:
        return provider_spec(provider).credential_required

    def ensure_kind_supported(
        self,
        provider: InferenceProvider,
        kind: InferenceModelKind,
    ) -> None:
        if kind not in provider_spec(provider).supported_kinds:
            raise UnsupportedInferenceCombination(provider, kind)

    def create_embedding(self, model: ResolvedInferenceModel) -> EmbeddingClientPort:
        factory = provider_spec(model.provider).embedding_factory
        if factory is None:
            raise UnsupportedInferenceCombination(model.provider, model.model.kind)
        return factory(model, outbound_policy=self._outbound_policy)

    def create_model_client(
        self,
        model: ResolvedInferenceModel,
        *,
        thinking_enabled: bool = False,
    ) -> LLM:
        factory = provider_spec(model.provider).chat_factory
        if factory is None:
            raise UnsupportedInferenceCombination(model.provider, model.model.kind)
        return factory(
            model,
            thinking_enabled=thinking_enabled,
            outbound_policy=self._outbound_policy,
        )


class InfrastructureModelMetricsAdapter(ModelMetricsPort):
    def snapshot(self) -> ModelMetricsSnapshot:
        metrics = get_llm_metrics_snapshot()
        return ModelMetricsSnapshot(
            multimodal_request_total=metrics.multimodal_request_total,
            multimodal_fallback_total=metrics.multimodal_fallback_total,
            multimodal_fallback_by_reason=dict(metrics.multimodal_fallback_by_reason),
            multimodal_image_bytes_total=metrics.multimodal_image_bytes_total,
            multimodal_image_count=metrics.multimodal_image_count,
            resilience_events=get_resilience_metrics_snapshot(),
        )

    def record_resilience_event(
        self,
        event: str,
        model_id: str,
        provider: str,
    ) -> None:
        record_llm_resilience_event(event, model_id, provider)


class ResilientLLMFactoryAdapter:
    def __init__(
        self,
        *,
        breaker,
        provider_catalog: InferenceProviderCatalog,
        model_client_factory: ModelClientFactoryPort,
        metrics: ModelMetricsPort,
    ) -> None:
        self._breaker = breaker
        self._provider_catalog = provider_catalog
        self._model_client_factory = model_client_factory
        self._metrics = metrics

    def __call__(
        self,
        model: ResolvedInferenceModel,
        *,
        policy: ModelResiliencePolicy,
        thinking_enabled: bool,
        inference_model_service,
        scope: OwnerScope,
    ):
        return create_resilient_llm(
            model,
            policy=policy,
            thinking_enabled=thinking_enabled,
            inference_model_service=inference_model_service,
            scope=scope,
            breaker=self._breaker,
            provider_catalog=self._provider_catalog,
            model_client_factory=self._model_client_factory,
            metrics=self._metrics,
        )
