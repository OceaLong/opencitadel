"""Application-facing metric capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.models.patrol import PatrolCheckResult, PatrolFinding, PatrolRun


@dataclass(frozen=True)
class ModelMetricsSnapshot:
    multimodal_request_total: int = 0
    multimodal_fallback_total: int = 0
    multimodal_fallback_by_reason: dict[str, int] = field(default_factory=dict)
    multimodal_image_bytes_total: int = 0
    multimodal_image_count: int = 0
    resilience_events: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionMetricsSnapshot:
    inbox_rows: dict[str, int]
    inbox_oldest_age_seconds: dict[str, float]
    outbox_lag_seconds: float
    outbox_redeliveries: int
    timer_lag_seconds: float
    activity_rows: dict[str, int]
    activity_oldest_age_seconds: dict[str, float]
    projector_cursor_lag: dict[str, int]


@runtime_checkable
class ExecutionMetricsPort(Protocol):
    async def refresh(self, *, now: datetime) -> ExecutionMetricsSnapshot: ...


@runtime_checkable
class ModelMetricsPort(Protocol):
    def snapshot(self) -> ModelMetricsSnapshot: ...

    def record_resilience_event(
        self,
        event: str,
        model_id: str,
        provider: str,
    ) -> None: ...


@runtime_checkable
class GovernanceMetricsPort(Protocol):
    def record_chain_verification(self, result: str) -> None: ...

    def record_remediation_transition(self, status: str) -> None: ...

    def observe_patrol_finalized(
        self,
        run: PatrolRun,
        results: list[PatrolCheckResult],
        findings: list[PatrolFinding],
    ) -> None: ...
