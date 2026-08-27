from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.application.ports.inference import (
    CircuitBreakerPort,
    EmbeddingFactoryPort,
    InferenceProviderCatalog,
    ModelClientFactoryPort,
)
from app.application.ports.observability import GovernanceMetricsPort, ModelMetricsPort
from app.application.ports.reporting import EvidenceSignerPort, ReportRendererPort
from app.application.services.inference_status_service import InferenceStatusService
from app.domain.models.scope import OwnerScope
from app.infrastructure.adapters.inference_ports import (
    InfrastructureInferenceProviderAdapter,
    InfrastructureModelMetricsAdapter,
)
from app.infrastructure.adapters.reporting_ports import (
    HmacEvidenceSigner,
    MarkdownPdfRenderer,
)
from app.infrastructure.external.llm.circuit_breaker import LLMCircuitBreaker
from tests.app.application_test_support import FakeCircuitBreaker, FakeModelMetrics


def test_inference_ports_are_runtime_checkable() -> None:
    providers = InfrastructureInferenceProviderAdapter()

    assert isinstance(providers, InferenceProviderCatalog)
    assert isinstance(providers, EmbeddingFactoryPort)
    assert isinstance(providers, ModelClientFactoryPort)
    assert isinstance(InfrastructureModelMetricsAdapter(), ModelMetricsPort)
    assert isinstance(LLMCircuitBreaker(redis=object()), CircuitBreakerPort)


def test_reporting_ports_are_runtime_checkable() -> None:
    signer = HmacEvidenceSigner(key_id="audit-v1", secret="test-secret")

    assert isinstance(signer, EvidenceSignerPort)
    assert isinstance(MarkdownPdfRenderer(), ReportRendererPort)
    assert isinstance(_FakeGovernanceMetrics(), GovernanceMetricsPort)


def test_evidence_signer_uses_injected_key_material() -> None:
    payload = b'{"manifest":1}'
    signer = HmacEvidenceSigner(key_id="audit-v7", secret="test-secret")

    assert signer.key_id == "audit-v7"
    assert (
        signer.sign(payload)
        == hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()
    )


async def test_inference_status_uses_injected_breaker_and_metrics() -> None:
    models = SimpleNamespace(
        list_resolved_chat_models=AsyncMock(return_value=[SimpleNamespace(id="chat-model")])
    )
    capabilities = SimpleNamespace(
        get_capabilities=AsyncMock(
            return_value=SimpleNamespace(model_dump=lambda *, mode: {"mode": mode, "items": {}})
        )
    )
    policy_heads = SimpleNamespace(
        active_execution=AsyncMock(
            return_value=SimpleNamespace(
                revision=SimpleNamespace(policy=SimpleNamespace(model_resilience=object()))
            )
        )
    )
    service = InferenceStatusService(
        models,
        capabilities,
        policy_heads,
        FakeCircuitBreaker(open_model_ids={"chat-model"}),
        FakeModelMetrics(),
    )

    status = await service.get_status(OwnerScope.personal("user-1"))

    assert status["capabilities"] == {"mode": "json", "items": {}}
    assert status["circuit_breakers"] == [{"model_id": "chat-model", "state": "open"}]
    assert status["metrics"] == {
        "multimodal_requests": 0,
        "multimodal_fallbacks": 0,
        "resilience_events": {},
    }


def test_targeted_application_services_do_not_import_infrastructure_or_settings() -> None:
    service_root = Path(__file__).resolve().parents[4] / "app/application/services"
    names = (
        "embedding_service.py",
        "inference_model_service.py",
        "inference_endpoint_service.py",
        "inference_status_service.py",
        "a2a_server_service.py",
        "evidence_service.py",
        "compliance_service.py",
        "patrol_evidence_service.py",
        "patrol_run_service.py",
        "patrol_remediation_service.py",
        "audit_service.py",
    )
    sources = "\n".join((service_root / name).read_text() for name in names)

    for forbidden in (
        "app.infrastructure.external.inference.registry",
        "app.infrastructure.external.llm.circuit_breaker",
        "app.infrastructure.external.report.pdf_renderer",
        "app.infrastructure.observability",
    ):
        assert forbidden not in sources
    assert "core.config" not in sources


class _FakeGovernanceMetrics:
    def record_chain_verification(self, result: str) -> None:
        del result

    def record_remediation_transition(self, status: str) -> None:
        del status

    def observe_patrol_finalized(self, run, results, findings) -> None:
        del run, results, findings
