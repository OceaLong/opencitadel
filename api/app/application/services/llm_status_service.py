#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only LLM / embedding health aggregation for /api/llm/status."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.application.services.llm_model_service import LLMModelService
from app.infrastructure.external.llm.circuit_breaker import get_llm_circuit_breaker
from app.infrastructure.observability.llm_metrics import get_llm_metrics_snapshot, get_resilience_metrics_snapshot
from app.application.services.config_provider import get_runtime_config
from core.config import get_settings


class LLMStatusService:
    """Phase-1: config validity + breaker state; no live model probes."""

    def __init__(self, llm_model_service: LLMModelService) -> None:
        self._llm_model_service = llm_model_service
        self._breaker = get_llm_circuit_breaker()

    async def get_status(self) -> Dict[str, Any]:
        settings = get_settings()
        runtime = get_runtime_config()
        default_model = await self._llm_model_service.get_default_model()
        models = await self._llm_model_service.list_models(mask=True)

        default_info: Optional[Dict[str, Any]] = None
        overall = "not_configured"
        if default_model:
            has_key = bool(default_model.api_key.strip()) or default_model.provider.value == "ollama"
            default_info = {
                "model_id": default_model.id,
                "display_name": default_model.display_name,
                "provider": default_model.provider.value,
                "base_url_configured": bool(default_model.base_url.strip()),
                "api_key_configured": has_key,
            }
            overall = "configured"
            breaker = await self._breaker.get_state(default_model.id)
            if breaker.value == "open":
                overall = "degraded"
            elif breaker.value == "half_open":
                overall = "degraded"

        embedding = {
            "api_key_configured": bool(settings.embedding_api_key.strip()),
            "vector_enabled": runtime.memory.vector_enabled,
            "enabled": runtime.feature_flags.enable_embeddings and bool(settings.embedding_api_key.strip()),
        }

        breakers: List[Dict[str, Any]] = []
        for model in models:
            snap = await self._breaker.snapshot(model.id)
            breakers.append(snap)
            if snap.get("state") == "open" and overall == "configured":
                overall = "degraded"

        metrics = get_llm_metrics_snapshot()
        return {
            "status": overall,
            "default_model": default_info,
            "embedding": embedding,
            "circuit_breakers": breakers,
            "metrics": {
                "multimodal_requests": metrics.multimodal_request_total,
                "multimodal_fallbacks": metrics.multimodal_fallback_total,
                "resilience_events": get_resilience_metrics_snapshot(),
            },
            "feature_flags": runtime.feature_flags.model_dump(),
            "model_resilience": {
                "enabled": runtime.model_resilience.enabled,
                "fallback_enabled": runtime.model_resilience.fallback_enabled,
            },
        }
