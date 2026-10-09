"""Offline shared fixed-slot/resource completion regressions."""

import copy

import pytest
from capacity_synthetic import transcript
from scripts.acceptance.capacity_models import Network, Window, WindowPlan
from scripts.acceptance.capacity_network import validate_calibrations


@pytest.fixture(scope="module")
def calibration_facts():
    roles, _ = transcript()
    return (
        Network.model_validate(roles["network"]),
        {w["window_id"]: Window.model_validate(w) for w in roles["workload"]["windows"]},
        {w["window_id"]: WindowPlan.model_validate(w) for w in roles["protocol"]["windows"]},
    )


@pytest.mark.parametrize("defect", ["late_dispatch", "late_control_completion"])
def test_rejects_calibration_phase_moved_within_whole_load(calibration_facts, defect):
    network, windows, plans = copy.deepcopy(calibration_facts)
    interval = next(
        r for r in network.phase_intervals if r.window_id == "window-0" and r.phase == "window"
    )
    if defect == "late_dispatch":
        interval.scheduled_ns += 10_000_000_000
        interval.deadline_ns += 10_000_000_000
        interval.before_ns += 10_000_000_000
        interval.after_ns += 10_000_000_000
        for row in network.calibrations:
            if row.window_id == "window-0" and row.phase == "window":
                row.start_ns += 10_000_000_000
                row.end_ns += 10_000_000_000
    else:
        interval.after_ns = interval.deadline_ns + 1
    with pytest.raises(ValueError, match="calibration"):
        validate_calibrations(network, windows, "host-clock", plans)


def test_complete_fixed_calibration_intervals_are_accepted(calibration_facts):
    network, windows, plans = calibration_facts
    validate_calibrations(network, windows, "host-clock", plans)


@pytest.mark.parametrize("defect", ["after_completion", "before_load", "future_frame"])
def test_shared_resource_observations_must_finish_inside_actual_load(calibration_facts, defect):
    from scripts.acceptance.capacity_completion import validate_completion
    from scripts.acceptance.capacity_models import Measurements, Plan

    roles, _ = transcript()
    plans = {p["sample_id"]: Plan.model_validate(p) for p in roles["protocol"]["samples"]}
    facts = Measurements.model_validate(roles["measurements"])
    _, windows, _ = calibration_facts
    row = facts.resources[0]
    window = windows[plans[row.sample_id].window_id]
    if defect == "after_completion":
        row.end_ns = window.coordinator_end_ns + 1
    elif defect == "before_load":
        row.start_ns = window.coordinator_start_ns - 1
    else:
        row.frames[-1].observed_ns = row.end_ns + 1
    with pytest.raises(ValueError, match=r"resource|frame"):
        validate_completion(plans, facts, "host-clock", windows)
