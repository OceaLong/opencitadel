from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.application.services.patrol_retention_service import PatrolRetentionService
from app.infrastructure.external.scheduler.job_scheduler import run_patrol_retention_tick


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self._values


class _Db:
    def __init__(self):
        self.select_results = iter([["finding-1"], ["result-1", "result-2"], ["run-1"]])
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if getattr(statement, "is_select", False):
            return _ScalarResult(next(self.select_results))
        return SimpleNamespace()


class _Uow:
    def __init__(self, db):
        self.db_session = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_retention_purges_evidence_findings_and_runs_but_never_audit_rows():
    db = _Db()
    service = PatrolRetentionService(lambda: _Uow(db))
    result = await service.cleanup(
        run_days=30,
        finding_days=14,
        evidence_days=7,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    assert result == {
        "runs_deleted": 1,
        "findings_deleted": 1,
        "evidence_refs_purged": 2,
    }
    rendered = "\n".join(str(statement) for statement in db.statements).lower()
    assert "patrol_findings" in rendered
    assert "patrol_check_results" in rendered
    assert "patrol_runs" in rendered
    assert "audit" not in rendered


@pytest.mark.asyncio
async def test_patrol_retention_tick_is_lease_guarded():
    service = SimpleNamespace(cleanup=AsyncMock(return_value={"runs_deleted": 1}))
    with (
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.acquire_scheduler_lease",
            AsyncMock(return_value=True),
        ) as acquire,
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.release_scheduler_lease",
            AsyncMock(return_value=True),
        ) as release,
    ):
        result = await run_patrol_retention_tick(
            service,
            run_days=30,
            finding_days=30,
            evidence_days=7,
            batch_size=100,
            lease_seconds=15,
            owner_token="worker-1",
        )
    assert result == {"runs_deleted": 1}
    service.cleanup.assert_awaited_once_with(
        run_days=30,
        finding_days=30,
        evidence_days=7,
        batch_size=100,
    )
    acquire.assert_awaited_once()
    release.assert_awaited_once()
