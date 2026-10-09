"""Synthetic validator inputs only; never actual scheduler evidence."""

import copy

import pytest
from scripts.acceptance import strict_bridge


def evidence():
    return {
        "resource_ids": {
            "batch_id": "new",
            "first_result_id": "first",
            "blocked_result_id": "second",
            "run_id": "run",
            "suite_version": "suite",
            "call_identity": "call",
        },
        "before": {
            "preflight_allowed": True,
            "preflight_revision": 1,
            "token_budget": 100,
            "subject_bound": 100,
            "judge_bound": 100,
            "repeat": 2,
            "dispatch_limit": 1,
        },
        "after": {
            "usage": {"total_tokens": 5},
            "ledger": {"spent_tokens": 5, "reserved_tokens": 0, "slots": 0},
            "physical_sends": 1,
            "settlements": 1,
            "execution_status": "blocked_budget",
            "scoring_status": "skipped",
            "admission_receipt": False,
            "envelope": False,
            "prepared_envelope": False,
        },
    }


def test_accepts_real_shape_but_rejects_missing_usage_or_admitted_second_slot():
    strict_bridge.validate_scheduler_budget(evidence())
    for group, field, value in [
        ("after", "usage", {"total_tokens": 0}),
        ("after", "envelope", True),
        ("after", "admission_receipt", True),
        ("after", "prepared_envelope", True),
        ("before", "preflight_allowed", False),
        ("before", "token_budget", 99),
        ("before", "judge_bound", 101),
        ("after", "ledger", {"spent_tokens": 0, "reserved_tokens": 0, "slots": 0}),
    ]:
        item = copy.deepcopy(evidence())
        item[group][field] = value
        with pytest.raises(strict_bridge.BridgeError):
            strict_bridge.validate_scheduler_budget(item)
