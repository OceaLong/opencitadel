from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.ports.inference import CircuitBreakerPort
from app.application.ports.observability import ModelMetricsPort
from app.application.services.capability_service import CapabilityService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.models.scope import OwnerScope


class InferenceStatusService:
    def __init__(
        self,
        models: InferenceModelService,
        capabilities: CapabilityService,
        policy_heads: PolicyHeadReader,
        breaker: CircuitBreakerPort,
        metrics: ModelMetricsPort,
    ) -> None:
        self._models = models
        self._capabilities = capabilities
        self._policy_heads = policy_heads
        self._breaker = breaker
        self._metrics = metrics

    async def get_status(self, scope: OwnerScope) -> dict[str, Any]:
        models = await self._models.list_resolved_chat_models(scope=scope)
        active = await self._policy_heads.active_execution(
            require_fresh=True,
            now=datetime.now(UTC),
        )
        policy = active.revision.policy.model_resilience
        breakers = [await self._breaker.snapshot(model.id, policy) for model in models]
        capabilities = await self._capabilities.get_capabilities(scope)
        metrics = self._metrics.snapshot()
        return {
            "capabilities": capabilities.model_dump(mode="json"),
            "circuit_breakers": breakers,
            "metrics": {
                "multimodal_requests": metrics.multimodal_request_total,
                "multimodal_fallbacks": metrics.multimodal_fallback_total,
                "resilience_events": metrics.resilience_events,
            },
        }
