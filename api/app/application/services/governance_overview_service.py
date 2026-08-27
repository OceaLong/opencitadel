"""Admin governance dashboard read-model.

Aggregates already-recorded governance data (formal approvals, the audit
hash chain, Ops Patrol runs/findings/remediations) into a single
auditor/admin-facing overview document. Read-only: no new tables, no new
writes -- mirrors GovernanceProfileService's role for the per-session view,
but summarized across the whole platform for a time window.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.ports.queries import RunProjectionPort
from app.application.services.audit_service import AuditService
from app.domain.repositories.uow import IUnitOfWork

# The six PatrolRemediationStatus values (app.domain.models.patrol), spelled
# out here rather than imported as an enum so `by_status` always reports a
# stable, complete key set to the UI even for statuses with zero rows in the
# window -- `uow.patrol.remediation_status_counts` only returns rows that
# have data.
_REMEDIATION_STATUSES: tuple[str, ...] = (
    "proposed",
    "executing",
    "executed",
    "verified",
    "failed",
    "cancelled",
)


class GovernanceOverviewService:
    """Builds the admin governance overview dashboard payload."""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
        approval_projection: RunProjectionPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._approval_projection = approval_projection

    async def build_overview(self, *, days: int = 30) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=max(days, 1))

        approval_stats = await self._approval_projection.approval_stats(since)
        governance_daily = await self._approval_projection.governance_daily(since)
        async with self._uow_factory() as uow:
            patrol_daily = await uow.patrol.daily_run_finding_counts(since)
            remediation_counts = await uow.patrol.remediation_status_counts(since)

        chain_result = await self._audit_service.verify_chain()

        return {
            "approvals": _build_approval_stats(approval_stats),
            "interceptions": governance_daily,
            "patrol": patrol_daily,
            "remediation": _build_remediation_stats(remediation_counts),
            "chain": {
                "ok": chain_result.get("ok", False),
                "total": chain_result.get("total", 0),
                "first_broken_seq": chain_result.get("first_broken_seq"),
                "checked_at": chain_result.get("checked_at"),
            },
        }


def _build_approval_stats(stats: dict[str, Any]) -> dict[str, Any]:
    outcomes = stats.get("outcomes") or {}
    return {
        "pending_count": stats.get("pending_count", 0),
        "avg_decision_seconds": stats.get("avg_decision_seconds"),
        "outcomes": {
            "approved": outcomes.get("approved", 0),
            "rejected": outcomes.get("rejected", 0),
            "cancelled": outcomes.get("cancelled", 0),
        },
    }


def _build_remediation_stats(status_counts: dict[str, int]) -> dict[str, Any]:
    by_status = {status: status_counts.get(status, 0) for status in _REMEDIATION_STATUSES}
    denominator = by_status["executed"] + by_status["verified"] + by_status["failed"]
    success_rate: float | None = by_status["verified"] / denominator if denominator > 0 else None
    return {"by_status": by_status, "success_rate": success_rate}
