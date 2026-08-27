import pytest

from app.application.services.governance_overview_service import (
    GovernanceOverviewService,
)


class _PatrolRepo:
    async def daily_run_finding_counts(self, since):
        return [{"date": "2026-08-24", "runs": 2, "findings": 1}]

    async def remediation_status_counts(self, since):
        return {"verified": 2, "failed": 1, "proposed": 3}


class _Uow:
    patrol = _PatrolRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Audit:
    async def verify_chain(self):
        return {
            "ok": True,
            "total": 8,
            "first_broken_seq": None,
            "checked_at": "2026-08-24T00:00:00Z",
        }


class _Projection:
    async def approval_stats(self, since):
        return {
            "pending_count": 1,
            "avg_decision_seconds": 5.5,
            "outcomes": {"approved": 4, "rejected": 2},
        }

    async def governance_daily(self, since):
        return [
            {
                "date": "2026-08-24",
                "approval_requests": 3,
                "activity_failures": 1,
            }
        ]


@pytest.mark.asyncio
async def test_overview_uses_formal_approval_and_activity_projection():
    service = GovernanceOverviewService(
        lambda: _Uow(),
        _Audit(),
        _Projection(),
    )

    overview = await service.build_overview(days=7)

    assert overview["approvals"] == {
        "pending_count": 1,
        "avg_decision_seconds": 5.5,
        "outcomes": {"approved": 4, "rejected": 2, "cancelled": 0},
    }
    assert overview["interceptions"] == [
        {
            "date": "2026-08-24",
            "approval_requests": 3,
            "activity_failures": 1,
        }
    ]
    assert overview["remediation"]["success_rate"] == pytest.approx(2 / 3)
    assert overview["chain"]["ok"] is True
