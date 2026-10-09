"""Strict current-invocation assertions over the real mounted producer artifacts.

The serial runner/bootstrap performs the SQL, broker/provider and child-process
work. These tests consume that exact invocation; there is no isolated fake DB,
pytest.skip, automatic service startup, or historical report fallback. Collection
is safe without environment/services; executing without the runner artifacts fails.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.acceptance.strict_bridge import read_json, validate_evidence  # noqa: E402


@pytest.fixture(scope="module")
def strict_execution_report():
    directory = os.environ.get("ACCEPTANCE_EVIDENCE_DIR")
    invocation = os.environ.get("ACCEPTANCE_STRICT_INVOCATION_ID")
    if not directory or not invocation:
        pytest.fail("requires the current owned acceptance runner invocation; no skip fallback")
    root = Path(directory)
    binding = read_json(root / "strict-binding.json")
    assert binding["invocation_id"] == invocation
    assert binding["run_id"] == os.environ["ACCEPTANCE_RUN_ID"]
    assert binding["project"] == os.environ["ACCEPTANCE_PROJECT_ID"]
    report = read_json(root / "strict-report.json")
    receipt = validate_evidence(report, binding, read_json(root / "strict-bootstrap.json"), root)
    assert read_json(root / "strict-consumer.json") == {**receipt, "kernel_restored": True}
    bootstrap = read_json(root / "strict-bootstrap.json")
    assert (
        owned(report, "AC19", "seven_daily_buckets")["resource_ids"]["session_id"]
        == bootstrap["analysis_session_id"]
    )
    return report


def owned(report, requirement, identity):
    return next(
        item
        for item in report["scenarios"]
        if (item["requirement"], item["id"]) == (requirement, identity)
    )


def test_ac02_parallel_ties_missing_parent_and_business_failure(strict_execution_report):
    parallel = owned(strict_execution_report, "AC02", "parallel_order")
    after = parallel["after"]
    assert after["run"]["duration_ms"] == 3000
    assert len(after["steps"]) == 3
    assert len({step["activity_id"] for step in after["steps"]}) == 3
    assert after["run"]["status"] == "completed"
    missing = [step for step in after["steps"] if step["business_outcome"] == "failure"]
    assert len(missing) == 1
    assert missing[0]["parent_step_id"] is None
    assert missing[0]["relationship"] == "unknown"
    assert "parent_step_id" in missing[0]["completeness"]["missing_fields"]


def test_ac05_real_shadow_and_explicit_historical_gap(strict_execution_report):
    shadow = owned(strict_execution_report, "AC05", "shadow_activation")
    assert (
        shadow["before"]["run"]["projection_revision"]
        == shadow["after"]["run"]["projection_revision"]
    )
    missing = owned(strict_execution_report, "AC05", "missing_history_available_segment")
    assert missing["database"]["main_database_distinct"] is True
    view = missing["after"]["view"]
    assert view["run"]["completeness"]["state"] == "partial"
    assert any(
        interval["reason"] == "pre_journal_progress_unavailable"
        for interval in view["run"]["completeness"]["missing_intervals"]
    )
    assert view["steps"]
    assert missing["before"]["migration"] != missing["after"]["migration"]


def test_ac12_real_reset_failure_process_death_and_late_cancel(strict_execution_report):
    reset = owned(strict_execution_report, "AC12", "reset_failure")
    assert reset["after"]["quarantine_state"] == "quarantine"
    assert reset["cleanup"]["state"] == "verified_clean"
    death = owned(strict_execution_report, "AC12", "worker_death")
    assert death["fault"]["mechanism"] == "child_process_termination"
    assert death["after"]["child_exit_code"] == -9
    assert death["after"]["quarantined"] is True
    assert death["cleanup"]["state"] == "verified_clean"
    late = owned(strict_execution_report, "AC12", "late_completion_cancel")
    assert late["after"]["physical_sends"] == 1
    assert late["after"]["ledger"]["reserved_tokens"] == 0
    assert any(
        check["id"] == "late_settlement_does_not_revive_batch" and check["passed"] is True
        for check in late["assertions"]
    )


def test_ac13_concurrency_admission_restart_lease_and_budget(strict_execution_report):
    submitted = owned(strict_execution_report, "AC13", "concurrent_submit")
    assert submitted["after"]["first_id"] == submitted["after"]["second_id"]
    lease = owned(strict_execution_report, "AC13", "lease_transfer")
    assert lease["after"]["generation"] > lease["before"]["generation"]
    admission = owned(strict_execution_report, "AC13", "admission_restart")
    assert admission["fault"]["mechanism"] == "boundary_exception_and_new_scheduler"
    assert admission["after"]["same_envelope"] is True
    stopped = owned(strict_execution_report, "AC13", "scheduler_budget_stop")
    from scripts.acceptance.strict_bridge import validate_scheduler_budget

    validate_scheduler_budget(stopped)
    assert stopped["after"]["cleanup_batch_status"] == "cancelled"
    assert any(
        item["id"] == "budget_reason_retained_after_cleanup" and item["passed"]
        for item in stopped["assertions"]
    )
    budget = owned(strict_execution_report, "AC13", "budget_exhaustion")
    assert budget["before"]["reserved"]["reserved_tokens"] == 266240
    assert budget["before"]["attempted"] == 2
    assert budget["before"]["denied"] == 1
    assert budget["after"]["ledger"]["spent_tokens"] == budget["after"]["usage"]["total_tokens"]


def test_ac22_complete_old_stream_new_events_and_old_entrypoint(strict_execution_report):
    legacy = owned(strict_execution_report, "AC22", "upcast_hash_replay")
    assert legacy["database"]["historical_revision"] == "97ae6574c80ba6f7fd0d98778223c3fb3610ce07"
    assert legacy["before"]["events"] >= 5
    assert legacy["after"]["events"] > legacy["before"]["events"]
    assert {item["id"] for item in legacy["assertions"]} == {
        "old_hashes_unchanged",
        "old_replay_equal",
        "upcast_hashes_unchanged",
        "legacy_missing_fields_unknown",
        "formal_rebuild_equal",
    }
    assert all(item["passed"] is True for item in legacy["assertions"])
    assert (
        owned(strict_execution_report, "AC22", "old_entrypoint")["after"]["view"]["run"]["status"]
        == "completed"
    )
    assert legacy["cleanup"]["state"] == "disposed"


def test_ac19_controlled_clock_is_bound_to_distinct_owned_session(strict_execution_report):
    from collections import Counter
    from datetime import datetime

    from app.domain.analysis.metrics import calendar_bucket

    dated = owned(strict_execution_report, "AC19", "seven_daily_buckets")
    parallel = owned(strict_execution_report, "AC02", "parallel_order")
    assert dated["resource_ids"]["session_id"] != parallel["resource_ids"]["session_id"]
    assert len(set(dated["resource_ids"]["run_ids"])) == 7
    assert dated["after"]["timezone"] == "UTC"
    runs = [view["run"] for view in dated["after"]["views"]]
    assert len(runs) == 7
    assert {run["run_id"] for run in runs} == set(dated["resource_ids"]["run_ids"])
    assert all(
        run["source"]["entity_type"] == "session"
        and run["source"]["entity_id"] == dated["resource_ids"]["session_id"]
        for run in runs
    )
    assert all(
        run["scope"]["owner_user_id"] == strict_execution_report["current_authority"]["user_id"]
        and run["scope"]["team_id"]
        == strict_execution_report["current_authority"]["scope"]["team_id"]
        for run in runs
    )
    buckets = Counter(
        calendar_bucket(datetime.fromisoformat(run["admitted_at"]), "day", "UTC")[0].isoformat()
        for run in runs
    )
    assert dict(buckets) == dated["after"]["source_bucket_counts"]
    assert len(buckets) == 7
    assert all(count == 1 for count in buckets.values())
    assert dated["cleanup"]["parent_session_id"] == dated["resource_ids"]["session_id"]
