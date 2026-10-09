"""Pure native authority transcripts; no API fixtures, browser or process launch."""

import pytest
from scripts.acceptance.capacity_completion import bind_native_target, progress_subsumption
from scripts.acceptance.capacity_models import NativeBinding, NativeIntent, Target


def intent():
    return NativeIntent(
        operation="first_screen",
        scope_id="scope",
        session_id="session",
        run_id="run",
        step_id=None,
        batch_id=None,
        view="task",
        history=None,
        filters=None,
    )


def binding(**changes):
    data = {
        "sample_id": "sample",
        "action_id": "action",
        "page_id": "page",
        "context_id": "context",
        "clock_id": "host",
        "request_id": "request",
        "response_id": "response",
        "request_ns": "11",
        "received_ns": "12",
        "target": Target(
            scope_id="scope", run_id="run", public_id="new-cut", revision="5", step_id=None
        ),
        "session_id": "session",
        "batch_id": None,
    }
    return NativeBinding(**(data | changes))


def test_first_issued_cut_binds_without_mutating_intent():
    plan = intent()
    before = plan.model_dump()
    assert (
        bind_native_target(
            plan,
            [binding()],
            sample_id="sample",
            action_id="action",
            page_id="page",
            context_id="context",
            clock_id="host",
            trigger_ns=10,
        ).public_id
        == "new-cut"
    )
    assert plan.model_dump() == before


@pytest.mark.parametrize("defect", ["missing", "duplicate", "old", "action", "scope", "run"])
def test_first_binding_rejects_missing_multiple_old_or_foreign(defect):
    row = binding()
    rows = [row]
    if defect == "missing":
        rows = []
    if defect == "duplicate":
        rows.append(row)
    if defect == "old":
        rows = [binding(request_ns="9")]
    if defect == "action":
        rows = [binding(action_id="other")]
    if defect in {"scope", "run"}:
        rows = [binding(target=row.target.model_copy(update={f"{defect}_id": "foreign"}))]
    with pytest.raises(ValueError, match=r"missing/multiple|foreign|predates"):
        bind_native_target(
            intent(),
            rows,
            sample_id="sample",
            action_id="action",
            page_id="page",
            context_id="context",
            clock_id="host",
            trigger_ns=10,
        )


def test_fresh_analysis_cannot_preregister_a_watermark_or_arbitrary_filter():
    data = intent().model_dump() | {
        "operation": "analysis",
        "session_id": None,
        "run_id": None,
        "filters": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-04-01T00:00:00Z",
            "timezone": "UTC",
            "grain": "day",
            "watermark": "old",
        },
    }
    with pytest.raises(ValueError, match="Extra inputs"):
        NativeIntent(**data)


def progress(sequence):
    from scripts.acceptance.capacity_models import Progress

    return Progress(
        phase="measured",
        progress_id=f"p{sequence}",
        window_id="w",
        run_id="r",
        activity_id="a",
        generation=0,
        claim_generation=1,
        boot_id="b",
        pid=1,
        marker_id="m",
        marker_sequence=1,
        marker_captured_ns=1,
        event_id=f"e{sequence}",
        sequence=sequence,
        before_ns=1,
        after_ns=2,
        ack=True,
        error=None,
        message=f"Received fragments: {sequence}",
        source_identity=f"e{sequence}",
        source_activity_id="a",
        source_generation=0,
        source_claim_generation=1,
        source_sequence=sequence,
        applied=True,
        observed_order=sequence,
        projection_revision=sequence,
        public_event_id=f"e{sequence}",
        public_run_id="r",
        public_message=f"Received fragments: {sequence}",
    )


def test_coalesced_progress_requires_complete_committed_same_claim_chain():
    from app.application.execution.view_facts import attempt_key

    rows = [progress(1), progress(2), progress(3)]
    assert progress_subsumption(
        rows,
        rows[-1],
        public_step_id=attempt_key("a", 0, 1),
        public_revision=4,
        public_message="Received fragments: 3",
    ) == ["p1", "p2", "p3"]


@pytest.mark.parametrize("defect", ["gap", "reverse", "claim", "public", "not_applied", "future"])
def test_coalesced_progress_cannot_guess_subsumption(defect):
    from app.application.execution.view_facts import attempt_key

    rows = [progress(1), progress(2), progress(3)]
    painted = rows[-1]
    if defect == "gap":
        rows.pop(1)
    if defect == "reverse":
        rows.reverse()
    if defect == "claim":
        rows[1] = rows[1].model_copy(update={"claim_generation": 2})
    if defect == "public":
        rows[1] = rows[1].model_copy(update={"public_message": "other"})
    if defect == "not_applied":
        rows[1] = rows[1].model_copy(update={"applied": False})
    if defect == "future":
        painted = rows[1]
    with pytest.raises(ValueError, match=r"subsumption|progress|future"):
        progress_subsumption(
            rows,
            painted,
            public_step_id=attempt_key("a", 0, 1),
            public_revision=4,
            public_message=painted.public_message,
        )


def test_native_clock_keeps_uncertainty_and_rejects_boundary_selection():
    from types import SimpleNamespace

    from scripts.acceptance.capacity_completion import native_clock_bounds, native_slot_membership

    anchors = [SimpleNamespace(local_ms=5, host_before_ns="10000000", host_after_ns="10000100")]
    bounds = native_clock_bounds(6, anchors)
    assert bounds == (11000000, 11000100)
    assert native_slot_membership(bounds, 10900000, 12000000) == "inside"
    assert native_slot_membership(bounds, 12000000, 13000000) == "outside"
    with pytest.raises(ValueError, match="uncertainty"):
        native_slot_membership(bounds, 11000050, 12000000)


def test_generated_schema_is_exact_shared_python_authority():
    import json
    from pathlib import Path

    from scripts.acceptance.capacity_models import (
        NativeCommand,
        NativeControl,
        NativeFailureSnapshot,
        NativeRecord,
    )

    root = Path(__file__).resolve().parents[3]
    actual = json.loads((root / "e2e/performance/native-schema.json").read_text())
    assert actual == {
        k: model.model_json_schema()
        for k, model in {
            "command": NativeCommand,
            "record": NativeRecord,
            "control": NativeControl,
            "failure": NativeFailureSnapshot,
        }.items()
    }


def test_native_retention_geometry_and_original_provenance():
    from scripts.acceptance.capacity_models import NativeCapture

    original = {
        "kind": "capture",
        "capture_id": "c",
        "request_id": "r",
        "target_id": "t",
        "readback_sequence": 1,
        "renderer_candidates": [{"pid": 1, "start": "p"}],
        "dispatched_ns": "1",
        "received_ns": "2",
        "postcheck_ns": "3",
        "sha256": "a" * 64,
        "bytes": 100,
        "chunks": 1,
        "width": 1440,
        "height": 900,
        "qualification": "matched-reviewed-build",
        "retention": "full",
        "retained_sha256": "a" * 64,
        "retained_bytes": 100,
        "crop_rect": None,
        "transform": "png-native-full-v1",
        "channels": 3,
    }
    NativeCapture(**original)
    crop = original | {
        "retention": "progress-region",
        "retained_sha256": "b" * 64,
        "retained_bytes": 80,
        "crop_rect": [10, 10, 320, 64],
        "transform": "png-lossless-text-hull-pad4-v1",
    }
    NativeCapture(**crop)
    for invalid in [
        original | {"retained_sha256": "b" * 64},
        original | {"chunks": 2},
        crop | {"crop_rect": [10, 10, 321, 64]},
        crop | {"qualification": "pending-runtime-qualification"},
        crop | {"retained_bytes": 98305},
    ]:
        with pytest.raises(ValueError, match=r"retention|chunk|crop"):
            NativeCapture(**invalid)


def test_live_open_is_later_one_shot_control_not_future_command_fact():
    from scripts.acceptance.capacity_models import NativeCommand, NativeControl

    base = {
        "wire_version": 1,
        "command": "collect",
        "mode": "warm",
        "attempt_id": "a",
        "protocol_id": "p",
        "sample_id": "s",
        "action_id": "x",
        "context_id": "c",
        "page_id": "page",
        "window_id": "w",
        "clock_id": "host",
        "intent": intent().model_dump() | {"operation": "live_visible", "step_id": "step"},
        "deadline_ns": "1000",
        "window_open_ns": None,
        "resource_offset_ns": "10",
        "resource_duration_ns": "10",
        "require_positive_scroll": False,
        "max_captures": 121,
        "viewport_width": 1440,
        "viewport_height": 900,
        "collector_feature": "CDPScreenshotNewSurface",
    }
    NativeCommand(**base)
    with pytest.raises(ValueError, match="live open"):
        NativeCommand(**(base | {"window_open_ns": "100"}))
    control = {
        k: base[k]
        for k in (
            "wire_version",
            "attempt_id",
            "protocol_id",
            "sample_id",
            "action_id",
            "context_id",
            "page_id",
            "window_id",
            "clock_id",
        )
    }
    NativeControl(
        **(
            control
            | {
                "command": "open",
                "sequence": 1,
                "source_receipt_id": "actual-open",
                "progress": None,
                "opened_ns": "100",
            }
        )
    )


def native_record(observation):
    from scripts.acceptance.capacity_models import NativeRecord

    return NativeRecord(
        wire_version=1,
        attempt_id="a",
        protocol_id="p",
        sample_id="s",
        action_id="action",
        context_id="c",
        page_id="page",
        window_id="w",
        clock_id="host",
        sequence=1,
        received_ns="20",
        observation=observation,
    )


def test_shared_action_start_preserves_ack_overhead_and_rejects_foreign_owner():
    from scripts.acceptance.capacity_completion import native_action_trigger

    row = native_record({"kind": "action", "operation": "history", "trigger_ns": "10"})
    args = {
        "sample_id": "s",
        "action_id": "action",
        "context_id": "c",
        "page_id": "page",
        "clock_id": "host",
        "operation": "history",
        "first_request_ns": 30,
    }
    assert native_action_trigger(row, **args) == 10
    with pytest.raises(ValueError, match="owner"):
        native_action_trigger(row, **(args | {"action_id": "foreign"}))
    with pytest.raises(ValueError, match="precede"):
        native_action_trigger(row, **(args | {"first_request_ns": 15}))


@pytest.mark.parametrize("defect", [None, "loss", "partial", "late", "bytes"])
def test_shared_trace_requires_actual_complete_no_loss_and_exact_retained_bytes(defect):
    from scripts.acceptance.capacity_completion import validate_native_trace_completion

    data = {
        "kind": "trace-completion",
        "artifact_id": "native-trace",
        "stream_id": "stream",
        "end_dispatched_ns": "10",
        "end_received_ns": "12",
        "complete_received_ns": "11",
        "eof_received_ns": "15",
        "data_loss": False,
        "parser": "complete",
        "observed_bytes": 50,
        "retained_bytes": 50,
        "retained_chunks": 1,
        "retained_sha256": "a" * 64,
    }
    if defect == "loss":
        data["data_loss"] = True
    if defect == "partial":
        data["parser"] = "incomplete"
    args = {
        "clock_id": "host",
        "deadline_ns": 100,
        "retained_bytes": 50,
        "retained_chunks": 1,
        "retained_sha256": "a" * 64,
    }
    if defect == "late":
        args["deadline_ns"] = 20
    if defect == "bytes":
        args["retained_bytes"] = 49
    if defect is None:
        validate_native_trace_completion(native_record(data), **args)
    else:
        with pytest.raises(ValueError, match="native trace"):
            validate_native_trace_completion(native_record(data), **args)
