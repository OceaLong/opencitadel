from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.ports.queries import PatrolRetentionResult
from app.application.services.patrol_retention_service import PatrolRetentionService
from app.domain.runtime_policy import OperationsPolicy, PatrolRetentionPolicy
from app.infrastructure.external.scheduler.job_scheduler import run_patrol_retention_tick
from tests.runtime_policy_support import MutablePolicyReader


class _Store:
    def __init__(self) -> None:
        self.calls = []

    async def cleanup(self, **kwargs):
        self.calls.append(kwargs)
        return PatrolRetentionResult(1, 1, 2)


@pytest.mark.asyncio
async def test_retention_purges_evidence_findings_and_runs_but_never_audit_rows():
    store = _Store()
    service = PatrolRetentionService(
        store,
        policy_reader=MutablePolicyReader(
            operations=OperationsPolicy(
                patrol_retention=PatrolRetentionPolicy(
                    run_days=30,
                    finding_days=14,
                    collector_evidence_days=7,
                )
            )
        ),
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )
    result = await service.cleanup()
    assert result == {
        "runs_deleted": 1,
        "findings_deleted": 1,
        "evidence_refs_purged": 2,
    }
    assert store.calls == [
        {
            "run_cutoff": datetime(2026, 7, 4, tzinfo=UTC),
            "finding_cutoff": datetime(2026, 7, 20, tzinfo=UTC),
            "evidence_cutoff": datetime(2026, 7, 27, tzinfo=UTC),
            "limit": 100,
        }
    ]


@pytest.mark.asyncio
async def test_patrol_retention_tick_is_lease_guarded():
    service = SimpleNamespace(cleanup=AsyncMock(return_value={"runs_deleted": 1}))
    leases = SimpleNamespace(
        acquire=AsyncMock(return_value=True),
        renew=AsyncMock(return_value=True),
        release=AsyncMock(return_value=True),
    )
    result = await run_patrol_retention_tick(
        service,
        leases=leases,
        worker_id="worker-1",
        lease_seconds=15,
        owner_token="worker-1:tick-1",
    )
    assert result == {"runs_deleted": 1}
    service.cleanup.assert_awaited_once_with()
    leases.acquire.assert_awaited_once()
    leases.release.assert_awaited_once()
