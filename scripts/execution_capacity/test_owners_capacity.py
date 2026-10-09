"""Pure necessary-condition checks; these tests grant no source qualification."""

from dataclasses import fields
from inspect import signature

import pytest
from scripts.acceptance.capacity_derive import dimensions
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.observer_session import ObserverSession
from scripts.execution_capacity.owners_capacity import (
    assess_owners_lower_bound,
    assess_required_owners_floor,
)


def _limits(*, bytes_limit=2**40, rows_limit=2**40):
    return {
        "bytes_limit": bytes_limit,
        "rows_limit": rows_limit,
        "row_limit": 1024 * 1024,
        "index_bytes": 8 * 4096,
    }


def test_fixed_session_retention_and_inherited_work_limits_match_live_observer():
    root = EvidenceBudget(bytes_limit=2**40, rows_limit=2**40, row_limit=1024 * 1024)
    session = ObserverSession(budget=root)
    try:
        bound = assess_owners_lower_bound(_limits(), source_reads=1)
        assert (bound.session_bytes_limit, bound.session_rows_limit) == (
            session.evidence_budget.bytes_limit,
            session.evidence_budget.rows_limit,
        )
        assert session.evidence_budget.work_bytes_limit == root.work_bytes_limit
        assert session.evidence_budget.work_rows_limit == root.work_rows_limit
        assert bound.session_rows_limit == 16_384
        assert bound.session_bytes_limit == 16 * 1024 * 1024
    finally:
        session.close()


def test_100k_owners_and_1200_diagnostic_reads_have_exact_q_lower_bounds():
    bound = assess_owners_lower_bound(_limits(), source_reads=1200)
    assert bound.status == "infeasible"
    assert bound.source_reads_basis == "diagnostic-input"
    assert bound.source_reads_plan_bound is False
    assert bound.owner_rows_lower_bound == 100_000
    assert bound.permanent_rows_lower_bound_per_read == 100_000
    assert bound.query_bytes_lower_bound_per_read == 416_000_000
    assert bound.query_rows_lower_bound_per_read == 1_700_000
    assert bound.summed_query_bytes_lower_bound == 499_200_000_000
    assert bound.summed_query_rows_lower_bound == 2_040_000_000
    assert "permanent-owner-rows-exceed-fixed-session-child" in bound.blockers
    assert "current-unpaged-query-exceeds-fixed-session-child" in bound.blockers
    assert "leased-query-work-would-exceed-plan-inherited-work" not in bound.blockers
    assert not {"accepted", "full_source_ready"} & {field.name for field in fields(bound)}


def test_required_protocol_dimensions_are_100_baseline_and_1100_loaded():
    required = dimensions()
    assert len(required) == 16
    assert {
        (kind, mode): sum(
            count
            for (group, group_mode, _), count in required.items()
            if (group, group_mode) == (kind, mode)
        )
        for kind, mode, _ in required
    } == {
        ("standard", "warm"): 600,
        ("standard", "cold"): 60,
        ("step_capacity", "warm"): 300,
        ("step_capacity", "cold"): 40,
        ("admission", "baseline"): 100,
        ("admission", "loaded"): 100,
    }
    assert required[("admission", "baseline", "admission")] == 100
    assert sum(count for key, count in required.items() if key[1] == "baseline") == 100
    assert sum(count for key, count in required.items() if key[1] != "baseline") == 1100
    assert sum(required.values()) == 1200


def test_required_owners_floor_is_conditional_and_cannot_approve_fixed_child():
    bound = assess_required_owners_floor(_limits())
    assert bound.status == "infeasible"
    assert bound.source_reads == 1200
    assert bound.source_reads_basis == "conditional-protocol-floor"
    assert bound.source_reads_plan_bound is False
    assert "one-owners-read-per-required-sample-assumption" in bound.unresolved
    assert bound.owner_rows_lower_bound == 100_000
    assert bound.permanent_rows_lower_bound_per_read == 100_000
    assert bound.query_bytes_lower_bound_per_read == 416_000_000
    assert bound.query_rows_lower_bound_per_read == 1_700_000
    assert bound.summed_query_bytes_lower_bound == 499_200_000_000
    assert bound.summed_query_rows_lower_bound == 2_040_000_000
    assert bound.session_bytes_limit == 16 * 1024 * 1024
    assert bound.session_rows_limit == 16_384
    assert "permanent-owner-rows-exceed-fixed-session-child" in bound.blockers
    assert not {"accepted", "full_source_ready"} & {field.name for field in fields(bound)}


def test_required_floor_has_no_caller_read_count_or_report_path():
    assert list(signature(assess_required_owners_floor).parameters) == ["evidence_limits"]
    with pytest.raises(TypeError, match="source_reads"):
        assess_required_owners_floor(_limits(), source_reads=1)
    with pytest.raises(TypeError, match="report"):
        assess_required_owners_floor(_limits(), report="untrusted.json")


@pytest.mark.parametrize("page_size", [1, 128, 1024, 100_000])
def test_diagnostic_page_size_cannot_erase_permanent_or_cumulative_bounds(page_size):
    bound = assess_owners_lower_bound(_limits(), source_reads=1200, diagnostic_page_size=page_size)
    assert bound.status == "infeasible"
    assert bound.permanent_rows_lower_bound_per_read == 100_000
    assert bound.summed_query_bytes_lower_bound == 499_200_000_000
    assert bound.summed_query_rows_lower_bound == 2_040_000_000
    assert "permanent-owner-rows-exceed-fixed-session-child" in bound.blockers


def test_extra_owners_and_reads_only_increase_diagnostic_lower_bounds():
    base = assess_owners_lower_bound(_limits(), source_reads=1)
    larger_population = assess_owners_lower_bound(
        _limits(), source_reads=1, additional_owner_rows=5000
    )
    more_reads = assess_owners_lower_bound(_limits(), source_reads=2)
    assert (
        larger_population.query_bytes_lower_bound_per_read > base.query_bytes_lower_bound_per_read
    )
    assert larger_population.query_rows_lower_bound_per_read > base.query_rows_lower_bound_per_read
    assert (
        larger_population.permanent_rows_lower_bound_per_read
        > base.permanent_rows_lower_bound_per_read
    )
    assert more_reads.summed_query_bytes_lower_bound > base.summed_query_bytes_lower_bound
    assert more_reads.summed_query_rows_lower_bound > base.summed_query_rows_lower_bound
    assert more_reads.query_bytes_lower_bound_per_read == base.query_bytes_lower_bound_per_read


def test_plan_inherited_work_limit_is_a_separate_necessary_condition():
    bound = assess_owners_lower_bound(
        _limits(bytes_limit=32 * 1024 * 1024, rows_limit=200_000), source_reads=1
    )
    assert "leased-query-work-would-exceed-plan-inherited-work" in bound.blockers
    assert bound.status == "infeasible"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"source_reads": 0}, "source_reads"),
        ({"source_reads": True}, "source_reads"),
        ({"source_reads": 2**63}, "source_reads"),
        ({"additional_owner_rows": -1}, "additional_owner_rows"),
        ({"additional_owner_rows": False}, "additional_owner_rows"),
        ({"diagnostic_page_size": 0}, "diagnostic_page_size"),
        ({"diagnostic_page_size": True}, "diagnostic_page_size"),
        ({"source_reads": 2**63 - 1}, "summed query bytes"),
        ({"additional_owner_rows": 2**63 - 1}, "owner row lower bound"),
    ],
)
def test_malformed_or_overflowing_diagnostic_inputs_fail_closed(overrides, error):
    arguments = {"source_reads": 1, **overrides}
    with pytest.raises(ValueError, match=error):
        assess_owners_lower_bound(_limits(), **arguments)


def test_evidence_limits_use_the_original_closed_plan_parser():
    invalid = _limits()
    invalid["work_rows_limit"] = 2**40
    with pytest.raises(ValueError, match="exact supported immutable evidence limits"):
        assess_owners_lower_bound(invalid, source_reads=1)
