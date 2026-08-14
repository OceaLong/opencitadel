#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Admin governance dashboard read-model.

Aggregates already-recorded governance data (approval batches, the audit
hash chain, Ops Patrol runs/findings/remediations) into a single
auditor/admin-facing overview document. Read-only: no new tables, no new
writes -- mirrors GovernanceProfileService's role for the per-session view,
but summarized across the whole platform for a time window.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.application.services.audit_service import AuditService
from app.application.services.governance_profile_service import _APPROVAL_ACTIONS
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
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service

    async def build_overview(self, *, days: int = 30) -> Dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))

        async with self._uow_factory() as uow:
            approval_stats = await uow.resource_governance.approval_batch_stats(since)
            # "gate hits": governance decision rows (approve/reject/takeover/
            # rollback -- the same _APPROVAL_ACTIONS set GovernanceProfileService
            # uses for a session's `approvals` section) each only exist
            # because a gate intercepted the call and required a human
            # decision. "denials" are outright capability-policy rejections
            # (Task 2's `agent_tool_denied`). Together they are the daily
            # "governance intervened" trend.
            gate_hit_rows = await uow.audit.daily_action_counts(
                sorted(_APPROVAL_ACTIONS), since=since
            )
            denial_rows = await uow.audit.daily_action_counts(
                ["agent_tool_denied"], since=since
            )
            patrol_daily = await uow.patrol.daily_run_finding_counts(since)
            remediation_counts = await uow.patrol.remediation_status_counts(since)

        chain_result = await self._audit_service.verify_chain()

        return {
            "approvals": _build_approval_stats(approval_stats),
            "interceptions": _merge_daily_counts(gate_hit_rows, denial_rows),
            "patrol": patrol_daily,
            "remediation": _build_remediation_stats(remediation_counts),
            "chain": {
                "ok": chain_result.get("ok", False),
                "total": chain_result.get("total", 0),
                "first_broken_seq": chain_result.get("first_broken_seq"),
                "checked_at": chain_result.get("checked_at"),
            },
        }


def _build_approval_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = stats.get("outcomes") or {}
    return {
        "pending_count": stats.get("pending_count", 0),
        "avg_decision_seconds": stats.get("avg_decision_seconds"),
        "outcomes": {
            "approved": outcomes.get("approved", 0),
            "rejected": outcomes.get("rejected", 0),
            "expired": outcomes.get("expired", 0),
            "consumed": outcomes.get("consumed", 0),
        },
    }


def _merge_daily_counts(
        gate_hit_rows: List[Dict[str, Any]],
        denial_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sum per-(date, action) rows into one {date, approval_decisions,
    denials} row per date. Only dates with at least one approval decision
    or denial are returned -- filling the full date axis (including zero
    days) is the frontend's job, per the task-5 brief's gap-fill policy."""
    merged: Dict[str, Dict[str, int]] = {}
    for row in gate_hit_rows:
        bucket = merged.setdefault(row["date"], {"approval_decisions": 0, "denials": 0})
        bucket["approval_decisions"] += row["count"]
    for row in denial_rows:
        bucket = merged.setdefault(row["date"], {"approval_decisions": 0, "denials": 0})
        bucket["denials"] += row["count"]
    return [
        {"date": date, "approval_decisions": counts["approval_decisions"], "denials": counts["denials"]}
        for date, counts in sorted(merged.items())
    ]


def _build_remediation_stats(status_counts: Dict[str, int]) -> Dict[str, Any]:
    by_status = {status: status_counts.get(status, 0) for status in _REMEDIATION_STATUSES}
    denominator = by_status["executed"] + by_status["verified"] + by_status["failed"]
    success_rate: Optional[float] = (
        by_status["verified"] / denominator if denominator > 0 else None
    )
    return {"by_status": by_status, "success_rate": success_rate}
