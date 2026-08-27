"""Policy-driven Patrol retention orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.ports.queries import PatrolRetentionStorePort
from app.application.services.runtime_policy_reader import OperationsPolicyReader


class PatrolRetentionService:
    def __init__(
        self,
        store: PatrolRetentionStorePort,
        *,
        policy_reader: OperationsPolicyReader,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._policy_reader = policy_reader
        self._clock = clock or (lambda: datetime.now(UTC))

    async def cleanup(self) -> dict[str, int]:
        reference = self._clock()
        if reference.tzinfo is None:
            raise ValueError("Patrol retention clock must be timezone-aware")
        reference = reference.astimezone(UTC)
        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=reference,
        )
        policy = active.revision.policy.patrol_retention
        result = await self._store.cleanup(
            run_cutoff=reference - timedelta(days=policy.run_days),
            finding_cutoff=reference - timedelta(days=policy.finding_days),
            evidence_cutoff=reference - timedelta(days=policy.collector_evidence_days),
            limit=policy.cleanup_batch_size,
        )
        return {
            "runs_deleted": result.runs_deleted,
            "findings_deleted": result.findings_deleted,
            "evidence_refs_purged": result.evidence_refs_purged,
        }
