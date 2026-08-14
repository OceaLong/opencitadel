#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GovernanceOverviewService: admin governance dashboard read-model.

Fake uow/repo shapes mirror test_governance_profile_service.py -- fake
repositories fed by hand, no real DB. The four repository aggregate methods
(``daily_action_counts``, ``approval_batch_stats``,
``daily_run_finding_counts``, ``remediation_status_counts``) are stubbed here
with pre-computed sample rows; their actual SQL is covered by
tests/app/infrastructure/repositories/test_governance_overview_repositories.py.
"""
import pytest

from app.application.services.governance_overview_service import (
    GovernanceOverviewService,
)
from app.application.services.governance_profile_service import _APPROVAL_ACTIONS


class _FakeAuditRepo:
    def __init__(self):
        self.daily_action_counts_calls: list[tuple[tuple[str, ...], object]] = []
        self.rows_by_actions: dict[tuple[str, ...], list[dict]] = {}

    def set_rows(self, actions: list[str], rows: list[dict]) -> None:
        self.rows_by_actions[tuple(sorted(actions))] = rows

    async def daily_action_counts(self, actions, *, since=None):
        self.daily_action_counts_calls.append((tuple(actions), since))
        return self.rows_by_actions.get(tuple(sorted(actions)), [])


class _FakeResourceGovernanceRepo:
    def __init__(self):
        self.approval_stats: dict = {
            "pending_count": 0,
            "outcomes": {"approved": 0, "rejected": 0, "expired": 0, "consumed": 0},
            "avg_decision_seconds": None,
        }
        self.since_calls: list = []

    async def approval_batch_stats(self, since):
        self.since_calls.append(since)
        return self.approval_stats


class _FakePatrolRepo:
    def __init__(self):
        self.daily_rows: list[dict] = []
        self.remediation_counts: dict[str, int] = {}

    async def daily_run_finding_counts(self, since):
        return self.daily_rows

    async def remediation_status_counts(self, since):
        return self.remediation_counts


class _FakeUow:
    def __init__(self, audit, resource_governance, patrol):
        self.audit = audit
        self.resource_governance = resource_governance
        self.patrol = patrol

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeAuditService:
    def __init__(self, chain_result: dict):
        self._chain_result = chain_result
        self.verify_chain_calls = 0

    async def verify_chain(self):
        self.verify_chain_calls += 1
        return self._chain_result


class _OverviewEnv:
    def __init__(self):
        self.audit_repo = _FakeAuditRepo()
        self.resource_governance_repo = _FakeResourceGovernanceRepo()
        self.patrol_repo = _FakePatrolRepo()
        self._uow = _FakeUow(self.audit_repo, self.resource_governance_repo, self.patrol_repo)
        self.audit_service = _FakeAuditService(
            {"ok": True, "total": 42, "first_broken_seq": None, "checked_at": "2026-08-13T00:00:00Z"}
        )
        self.service = GovernanceOverviewService(lambda: self._uow, self.audit_service)


@pytest.fixture
def env():
    return _OverviewEnv()


@pytest.mark.asyncio
async def test_overview_merges_approval_decisions_and_denials_by_date(env):
    """interceptions[] merges the approval-action daily rows
    (approval_decisions) and the agent_tool_denied daily rows (denials)
    into one row per date."""
    env.audit_repo.set_rows(
        list(_APPROVAL_ACTIONS),
        [
            {"date": "2026-08-01", "action": "agent_tool_approve", "count": 2},
            {"date": "2026-08-01", "action": "agent_tool_reject", "count": 1},
            {"date": "2026-08-02", "action": "agent_tool_approve", "count": 3},
        ],
    )
    env.audit_repo.set_rows(
        ["agent_tool_denied"],
        [{"date": "2026-08-01", "action": "agent_tool_denied", "count": 5}],
    )

    overview = await env.service.build_overview(days=30)

    assert overview["interceptions"] == [
        {"date": "2026-08-01", "approval_decisions": 3, "denials": 5},
        {"date": "2026-08-02", "approval_decisions": 3, "denials": 0},
    ]


@pytest.mark.asyncio
async def test_overview_only_returns_dates_with_data_no_gap_fill(env):
    """Days without any gate hit or denial are simply absent -- gap-filling
    a full date axis is explicitly the frontend's job, not this service's."""
    env.audit_repo.set_rows(
        list(_APPROVAL_ACTIONS),
        [{"date": "2026-08-05", "action": "agent_tool_approve", "count": 1}],
    )

    overview = await env.service.build_overview(days=30)

    assert [row["date"] for row in overview["interceptions"]] == ["2026-08-05"]


@pytest.mark.asyncio
async def test_overview_passes_approval_batch_stats_through(env):
    env.resource_governance_repo.approval_stats = {
        "pending_count": 4,
        "outcomes": {"approved": 10, "rejected": 2, "expired": 1, "consumed": 8},
        "avg_decision_seconds": 37.5,
    }

    overview = await env.service.build_overview(days=30)

    assert overview["approvals"] == {
        "pending_count": 4,
        "avg_decision_seconds": 37.5,
        "outcomes": {"approved": 10, "rejected": 2, "expired": 1, "consumed": 8},
    }


@pytest.mark.asyncio
async def test_overview_passes_patrol_daily_stats_through(env):
    env.patrol_repo.daily_rows = [
        {"date": "2026-08-01", "runs": 2, "findings": 1},
        {"date": "2026-08-02", "runs": 1, "findings": 0},
    ]

    overview = await env.service.build_overview(days=30)

    assert overview["patrol"] == env.patrol_repo.daily_rows


@pytest.mark.asyncio
async def test_remediation_success_rate_is_verified_over_executed_verified_failed(env):
    env.patrol_repo.remediation_counts = {
        "proposed": 1,
        "executing": 2,
        "executed": 1,
        "verified": 6,
        "failed": 3,
        "cancelled": 1,
    }

    overview = await env.service.build_overview(days=30)

    assert overview["remediation"]["by_status"] == {
        "proposed": 1,
        "executing": 2,
        "executed": 1,
        "verified": 6,
        "failed": 3,
        "cancelled": 1,
    }
    # denominator = executed(1) + verified(6) + failed(3) = 10
    assert overview["remediation"]["success_rate"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_remediation_by_status_defaults_missing_statuses_to_zero(env):
    env.patrol_repo.remediation_counts = {"proposed": 2}

    overview = await env.service.build_overview(days=30)

    assert overview["remediation"]["by_status"] == {
        "proposed": 2,
        "executing": 0,
        "executed": 0,
        "verified": 0,
        "failed": 0,
        "cancelled": 0,
    }


@pytest.mark.asyncio
async def test_remediation_success_rate_is_none_when_denominator_is_zero(env):
    env.patrol_repo.remediation_counts = {"proposed": 3, "executing": 1}

    overview = await env.service.build_overview(days=30)

    assert overview["remediation"]["success_rate"] is None


@pytest.mark.asyncio
async def test_overview_includes_chain_status_from_audit_service(env):
    overview = await env.service.build_overview(days=30)

    assert overview["chain"] == {
        "ok": True,
        "total": 42,
        "first_broken_seq": None,
        "checked_at": "2026-08-13T00:00:00Z",
    }
    assert env.audit_service.verify_chain_calls == 1


@pytest.mark.asyncio
async def test_days_parameter_controls_the_since_cutoff_passed_to_repositories(env):
    await env.service.build_overview(days=7)

    since_values = {call for call in env.resource_governance_repo.since_calls}
    assert len(since_values) == 1
    since = since_values.pop()
    for actions, called_since in env.audit_repo.daily_action_counts_calls:
        assert called_since == since
