"""One-window test generator retains old facts with distinct physical owners."""

from capacity_population import slots
from capacity_population_window import marker_rows, network_rows, progress_rows, window_bundle
from scripts.acceptance.capacity_models import (
    Calibration,
    CalibrationPhaseInterval,
    LivePaint,
    Marker,
    Progress,
    SourceAck,
    Window,
    WindowPlan,
)


def test_finite_window_original_rows_have_distinct_round_and_model_shapes():
    first = (row for row in slots() if row.loaded_window_id is not None)
    left = next(first)
    right = next(first)
    plan, window = window_bundle(left)
    other_plan, other_window = window_bundle(right)
    WindowPlan.model_validate(plan)
    Window.model_validate(window)
    assert plan["window_id"] == window["round_origin"]["window_id"] == left.physical_window_id
    assert window["round_origin"]["sample_id"] == left.sample_id
    assert other_plan["window_id"] == other_window["round_origin"]["window_id"]
    assert window["window_id"] != other_window["window_id"]
    assert len(window["claims"]) == len(window["context_ids"]) == 10
    assert len(window["snapshots"]) == len(window["ticks"]) == 321


def test_finite_progress_stream_preserves_all_ten_runs_and_chunk_digest():
    slot = next(row for row in slots() if row.loaded_window_id is not None)
    count = 0
    for progress, ack, paint in progress_rows(slot):
        Progress.model_validate(progress)
        SourceAck.model_validate(ack)
        LivePaint.model_validate(paint)
        assert progress["progress_id"] == ack["progress_id"] == paint["progress_id"]
        assert progress["window_id"] == slot.loaded_window_id
        count += 1
    assert count == 600


def test_finite_window_markers_and_network_keep_complete_fixed_phase_counts():
    slot = next(row for row in slots() if row.loaded_window_id is not None)
    markers = list(marker_rows(slot))
    assert len(markers) == 81
    assert [row["sequence"] for row in markers] == list(range(1, 82))
    for row in markers:
        Marker.model_validate(row)
    counts = {"calibration": 0, "phase": 0}
    for kind, row in network_rows(slot):
        (Calibration if kind == "calibration" else CalibrationPhaseInterval).model_validate(row)
        counts[kind] += 1
    assert counts == {"calibration": 72, "phase": 4}
