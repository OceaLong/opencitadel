"""Pure live resource/latency separation, with literal review counterexamples."""

from types import SimpleNamespace as Facts

import pytest
from scripts.acceptance.capacity_completion import validate_completion


def live_facts():
    target = {"scope": "actual"}
    plan = Facts(
        sample_id="s",
        operation="live_visible",
        action_id="a",
        window_id="w",
        page_id="page",
        context_id="context",
        target=target,
        intent=None,
        dimension="standard",
        require_positive_scroll=False,
        live_resource_interval=Facts(offset_ns=100, duration_ns=1000),
    )
    sample = Facts(
        sample_id="s",
        clock_id="clock",
        action_id="a",
        status="ok",
        error=None,
        trigger_ns=2100,
        start_ns=2100,
        end_ns=2200,
        source_id="source",
        browser_id="browser",
    )
    source = Facts(
        source_id="source",
        sample_id="s",
        clock_id="clock",
        target=target,
        kind="progress",
        submitted_ns=2100,
        acknowledged_ns=2150,
        readback_ns=2160,
    )
    browser = Facts(
        browser_id="browser",
        sample_id="s",
        action_id="a",
        page_id="page",
        context_id="context",
        clock_id="clock",
        visible=True,
        readback_target=target,
        painted_target=target,
        completion="progress_painted",
        readback_observed_ns=2170,
        paint_observed_ns=2200,
    )
    resource = Facts(
        sample_id="s",
        clock_id="clock",
        context_id="context",
        visible=True,
        forced_gc=False,
        start_ns=1000,
        end_ns=2300,
        scroll_distance_px=100,
        scroll_start_ns=1000,
        scroll_end_ns=2300,
        frames=[Facts(observed_ns=1000 + i) for i in range(100)],
        heap_samples=[Facts(observed_ns=1050, bytes=100)],
        dom_samples=[Facts(observed_ns=1050, rows=10)],
        long_tasks=[],
    )
    measurements = Facts(
        native_bindings=[],
        samples=[sample],
        sources=[source],
        browsers=[browser],
        resources=[resource],
    )
    windows = {
        "w": Facts(host_ready_sent_ns=1000, coordinator_start_ns=2000, coordinator_end_ns=5000)
    }
    return plan, measurements, windows


def test_live_frames_before_actual_open_cannot_count_as_loaded_resources():
    plan, measurements, windows = live_facts()
    with pytest.raises(ValueError, match="resource"):
        validate_completion({"s": plan}, measurements, "clock", windows)


def early_paint_with_loaded_capture():
    plan, measurements, windows = live_facts()
    plan.live_resource_interval = Facts(offset_ns=100, duration_ns=1000)
    sample, source, browser = (
        measurements.samples[0],
        measurements.sources[0],
        measurements.browsers[0],
    )
    sample.start_ns = sample.trigger_ns = 1100
    sample.end_ns = 1200
    source.submitted_ns, source.acknowledged_ns, source.readback_ns = 1100, 1150, 1160
    browser.readback_observed_ns, browser.paint_observed_ns = 1170, 1200
    trace = measurements.resources[0]
    trace.start_ns, trace.end_ns = 2050, 3150
    trace.scroll_start_ns, trace.scroll_end_ns = 2100, 3100
    trace.frames = [
        Facts(observed_ns=t, interval_ms=1) for t in [2051, *range(2100, 3100, 10), 3140]
    ]
    trace.heap_samples = [
        Facts(observed_ns=t, bytes=v) for t, v in [(2051, 999), (2200, 100), (3140, 999)]
    ]
    trace.dom_samples = [
        Facts(observed_ns=t, rows=v) for t, v in [(2051, 999), (2200, 10), (3140, 999)]
    ]
    trace.long_tasks = [
        Facts(start_ns=a, end_ns=b)
        for a, b in [(2051, 2099), (2090, 2120), (3090, 3130), (3131, 3140)]
    ]
    return plan, measurements, windows


def test_preopen_live_paint_uses_complete_independent_loaded_resource_slot():
    plan, measurements, windows = early_paint_with_loaded_capture()
    result = validate_completion({"s": plan}, measurements, "clock", windows)["s"]
    assert len(result.frames) == 100
    assert result.heap_bytes == [100]
    assert result.mounted_rows == [10]
    assert result.long_tasks_ms == [0.00003, 0.00004]
    assert len(measurements.resources[0].frames) == 102
    assert len(measurements.resources[0].heap_samples) == 3


@pytest.mark.parametrize(
    "defect",
    [
        "late_start",
        "early_end",
        "future_end",
        "shifted_slot",
        "preload_raw",
        "padding_frames",
        "outside_heap",
        "outside_dom",
        "foreign_clock",
        "unknown_time",
    ],
)
def test_live_capture_cannot_shift_skip_pad_or_outlive_fixed_slot(defect):
    plan, facts, windows = early_paint_with_loaded_capture()
    trace = facts.resources[0]
    if defect == "late_start":
        trace.start_ns = 2101
    elif defect == "early_end":
        trace.end_ns = 3099
    elif defect == "future_end":
        trace.end_ns = 5001
    elif defect == "shifted_slot":
        plan.live_resource_interval.offset_ns = 1100
    elif defect == "preload_raw":
        trace.frames[0].observed_ns = 1999
    elif defect == "padding_frames":
        trace.frames = [Facts(observed_ns=2051 + i // 2, interval_ms=1) for i in range(100)]
    elif defect == "outside_heap":
        trace.heap_samples = [trace.heap_samples[0], trace.heap_samples[-1]]
    elif defect == "outside_dom":
        trace.dom_samples = [trace.dom_samples[0], trace.dom_samples[-1]]
    elif defect == "foreign_clock":
        trace.clock_id = "foreign"
    elif defect == "unknown_time":
        trace.heap_samples[1].observed_ns = None
    with pytest.raises((ValueError, TypeError), match=r"resource|frame|clock|timestamp|NoneType"):
        validate_completion({"s": plan}, facts, "clock", windows)


@pytest.mark.parametrize(
    "rule", [None, {"offset_ns": 0, "duration_ns": 1000}, {"offset_ns": 100, "duration_ns": 0}]
)
def test_live_resource_rule_cannot_be_missing_or_start_without_margin(rule):
    from scripts.acceptance.capacity_models import Plan

    plan = {
        "sample_id": "s",
        "dimension": "standard",
        "mode": "warm",
        "operation": "live_visible",
        "ordinal": 0,
        "target": {
            "scope_id": "scope",
            "run_id": "run",
            "public_id": "public",
            "revision": "1",
            "step_id": None,
        },
        "window_id": "w",
        "reset_id": None,
        "physical_window_id": "w",
        "prewarm_completed_ns": 1,
        "action_id": "a",
        "page_id": "page",
        "context_id": "context",
        "live_resource_interval": rule,
    }
    with pytest.raises(ValueError, match=r"resource|offset|duration"):
        Plan.model_validate(plan)


def test_infeasible_live_resource_slot_is_rejected_before_effects():
    from scripts.acceptance.capacity_completion import validate_resource_plan

    plan, _, _ = live_facts()
    with pytest.raises(ValueError, match="resource slot"):
        validate_resource_plan(plan, Facts(measurement=Facts(end_offset_ns=1099)))
    validate_resource_plan(plan, Facts(measurement=Facts(end_offset_ns=1100)))


@pytest.mark.parametrize(
    ("model", "value"),
    [("HeapSample", {"bytes": 1}), ("DOMSample", {"rows": 1}), ("LongTask", {"end_ns": 1})],
)
def test_resource_values_without_actual_timestamps_are_not_evidence(model, value):
    from scripts.acceptance import capacity_models

    with pytest.raises(ValueError, match="required"):
        getattr(capacity_models, model).model_validate(value)
