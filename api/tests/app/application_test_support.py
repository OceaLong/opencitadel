from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from app.application.ports.observability import ModelMetricsSnapshot
from app.application.ports.queries import AuditSummary


class NoopGovernanceMetrics:
    def record_chain_verification(self, result: str) -> None:
        del result

    def record_remediation_transition(self, status: str) -> None:
        del status

    def observe_patrol_finalized(self, run, results, findings) -> None:
        del run, results, findings


class RecordingGovernanceMetrics:
    def __init__(self) -> None:
        self.chain_verifications: list[str] = []
        self.remediation_transitions: list[str] = []
        self.finalized_runs: list[tuple[object, list[object], list[object]]] = []

    def record_chain_verification(self, result: str) -> None:
        self.chain_verifications.append(result)

    def record_remediation_transition(self, status: str) -> None:
        self.remediation_transitions.append(status)

    def observe_patrol_finalized(self, run, results, findings) -> None:
        self.finalized_runs.append((run, results, findings))


@dataclass
class FakeModelMetrics:
    value: ModelMetricsSnapshot = field(default_factory=ModelMetricsSnapshot)
    events: list[tuple[str, str, str]] = field(default_factory=list)

    def snapshot(self) -> ModelMetricsSnapshot:
        return self.value

    def record_resilience_event(self, event: str, model_id: str, provider: str) -> None:
        self.events.append((event, model_id, provider))


class FakeCircuitBreaker:
    def __init__(self, *, open_model_ids: set[str] | None = None) -> None:
        self.open_model_ids = open_model_ids or set()

    async def is_open(self, model_id, policy) -> bool:
        del policy
        return model_id in self.open_model_ids

    async def allow_request(self, model_id, policy) -> str:
        return "deny" if await self.is_open(model_id, policy) else "allow"

    async def record_success(self, model_id, policy) -> None:
        del model_id, policy

    async def record_failure(self, model_id, error, policy) -> None:
        del model_id, error, policy

    async def snapshot(self, model_id, policy) -> dict[str, object]:
        return {
            "model_id": model_id,
            "state": "open" if await self.is_open(model_id, policy) else "closed",
        }


class FakeReportRenderer:
    def __init__(self, result: bytes | None = b"%PDF-test") -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def render_pdf(self, *, markdown: str, title: str) -> bytes | None:
        self.calls.append((markdown, title))
        return self.result


class FixedEvidenceSigner:
    def __init__(self, *, key_id: str = "test-key", secret: str = "test-secret") -> None:
        self._key_id = key_id
        self._secret = secret.encode()

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


class EmptyAuditSummaryQuery:
    async def summarize(self, *, start_at=None, end_at=None) -> AuditSummary:
        del start_at, end_at
        return AuditSummary(by_day=(), by_action=())


class EmptyEvidenceSessionQuery:
    async def list_sessions(self, *, limit: int, offset: int):
        del limit, offset
        return ()
