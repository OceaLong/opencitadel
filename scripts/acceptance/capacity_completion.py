"""Shared immutable operation/identity and completed resource-observation gate."""

from dataclasses import dataclass

from scripts.acceptance.capacity_models import Frame
from scripts.acceptance.capacity_physical import require, unique


@dataclass(frozen=True)
class ResourceStatistics:
    frames: list[Frame]
    heap_bytes: list[int]
    mounted_rows: list[int]
    long_tasks_ms: list[float]


def validate_resource_plan(plan, window):
    if plan.operation == "live_visible":
        slot = plan.live_resource_interval
        require(
            slot is not None
            and slot.offset_ns > 0
            and slot.duration_ns > 0
            and slot.offset_ns + slot.duration_ns <= window.measurement.end_offset_ns,
            "live resource slot cannot fit immutable measured window",
        )


def resource_statistics(plan, sample, trace, window):
    require(
        window.coordinator_start_ns <= trace.start_ns < trace.end_ns <= window.coordinator_end_ns,
        "resource capture outside actual load/completion interval",
    )
    if plan.operation == "live_visible":
        slot = plan.live_resource_interval
        require(
            slot is not None and slot.offset_ns > 0 and slot.duration_ns > 0,
            "immutable live resource interval missing",
        )
        start = window.coordinator_start_ns + slot.offset_ns
        end = start + slot.duration_ns
        require(
            trace.start_ns <= start < end <= trace.end_ns,
            "resource capture does not cover full immutable slot",
        )
    else:
        start, end = trace.start_ns, trace.end_ns
        require(
            start <= sample.start_ns <= sample.end_ns <= end,
            "resource interval does not cover sample",
        )
    require(
        trace.start_ns <= trace.scroll_start_ns < trace.scroll_end_ns <= trace.end_ns,
        "resource scroll outside actual capture",
    )
    require(
        all(trace.start_ns <= f.observed_ns <= trace.end_ns for f in trace.frames)
        and all(
            a.observed_ns < b.observed_ns
            for a, b in zip(trace.frames, trace.frames[1:], strict=False)
        ),
        "resource frame outside actual capture/order",
    )
    require(
        all(
            type(r.observed_ns) is int and trace.start_ns <= r.observed_ns <= trace.end_ns
            for r in [*trace.heap_samples, *trace.dom_samples]
        ),
        "resource sample timestamp outside actual capture",
    )
    require(
        all(trace.start_ns <= t.start_ns < t.end_ns <= trace.end_ns for t in trace.long_tasks),
        "resource long-task interval outside actual capture",
    )
    require(
        trace.scroll_distance_px > 0
        or (
            trace.scroll_nonoverflow
            and trace.scroll_client_height_px > 0
            and trace.scroll_height_px <= trace.scroll_client_height_px
            and plan.dimension != "step_capacity"
            and not plan.require_positive_scroll
        ),
        "zero scrolling lacks observed nonoverflow or mandatory probe scrolling",
    )
    # Membership is fixed by preregistered time, never a favorable chosen slice.
    # Retain the original complete capture; this only derives budget inputs.
    inside = (
        (lambda t: start <= t < end)
        if plan.operation == "live_visible"
        else (lambda t: start <= t <= end)
    )
    selected = ResourceStatistics(
        frames=[f for f in trace.frames if inside(f.observed_ns)],
        heap_bytes=[r.bytes for r in trace.heap_samples if inside(r.observed_ns)],
        mounted_rows=[r.rows for r in trace.dom_samples if inside(r.observed_ns)],
        long_tasks_ms=[
            (t.end_ns - t.start_ns) / 1_000_000
            for t in trace.long_tasks
            if t.start_ns < end and t.end_ns > start
        ],
    )
    require(
        len(selected.frames) >= 100 and selected.heap_bytes and selected.mounted_rows,
        "resource fixed interval lacks actual frame/heap/DOM observations",
    )
    return selected


COMPLETION = {
    "first_screen": ("interactive_summary", "summary_interactive"),
    "switch": ("selected_step", "selected_step_painted"),
    "history": ("history_revision", "history_painted"),
    "analysis": ("analysis_capture", "analysis_displayed"),
    "matrix": ("matrix_page", "matrix_usable"),
    "live_visible": ("progress", "progress_painted"),
    "admission": ("formal_admission", None),
}


def validate_completion(plans, measurements, clock_id, windows):
    samples = unique(measurements.samples, "sample_id")
    sources = unique(measurements.sources, "source_id")
    browsers = unique(measurements.browsers, "browser_id")
    resources = unique(measurements.resources, "sample_id")
    require(
        len(samples) == len(plans) and set(samples) == set(plans),
        "completion immutable sample coverage",
    )
    require(
        len(sources) == len(samples) and {s.sample_id for s in sources.values()} == set(samples),
        "completion unique source coverage",
    )
    visual = {p.sample_id for p in plans.values() if p.operation != "admission"}
    require(
        len(browsers) == len(visual)
        and {b.sample_id for b in browsers.values()} == visual
        and len(resources) == len(visual)
        and set(resources) == visual,
        "completion immutable native/resource coverage",
    )
    targets = resolved_targets(plans, measurements, clock_id)
    from scripts.acceptance.capacity_compact import CompactResources
    from scripts.acceptance.capacity_package import PublicUnique

    selected_resources = (
        CompactResources(resources._rows._owner) if type(resources) is PublicUnique else {}
    )
    for key, plan in plans.items():
        sample = samples[key]
        require(
            sample.clock_id == clock_id
            and sample.action_id == plan.action_id
            and sample.status == "ok"
            and sample.error is None
            and sample.trigger_ns == sample.start_ns <= sample.end_ns,
            "completion sample identity/clock/order",
        )
        require(sample.source_id in sources, "completion source identity missing")
        source = sources[sample.source_id]
        require(
            source.sample_id == key
            and source.clock_id == clock_id
            and source.target == targets[key]
            and source.kind == COMPLETION[plan.operation][0],
            "completion source identity/clock/operation",
        )
        require(
            sample.start_ns
            <= source.submitted_ns
            <= min(source.acknowledged_ns, source.readback_ns)
            and max(source.acknowledged_ns, source.readback_ns) <= sample.end_ns,
            "completion source observation unfinished",
        )
        if plan.window_id is not None:
            require(plan.window_id in windows, "completion load window missing")
            window = windows[plan.window_id]
            lower = (
                window.host_ready_sent_ns
                if plan.operation == "live_visible"
                else window.coordinator_start_ns
            )
            require(
                lower <= sample.start_ns <= sample.end_ns <= window.coordinator_end_ns,
                "completion sample outside load/completion interval",
            )
        if plan.operation == "admission":
            require(sample.browser_id is None, "admission unexpected browser")
            continue
        require(sample.browser_id in browsers, "completion native paint missing")
        browser = browsers[sample.browser_id]
        require(
            (
                browser.sample_id,
                browser.action_id,
                browser.page_id,
                browser.context_id,
                browser.clock_id,
            )
            == (key, plan.action_id, plan.page_id, plan.context_id, clock_id),
            "completion browser identity/clock",
        )
        require(
            browser.visible
            and browser.readback_target == browser.painted_target == targets[key]
            and browser.completion == COMPLETION[plan.operation][1],
            "completion browser target/operation paint mismatch",
        )
        require(
            source.readback_ns
            <= browser.readback_observed_ns
            <= browser.paint_observed_ns
            <= sample.end_ns,
            "completion browser observation unfinished",
        )
        trace = resources[key]
        require(
            trace.clock_id == clock_id
            and trace.context_id == plan.context_id
            and trace.visible
            and not trace.forced_gc,
            "completion resource identity/clock",
        )
        require(plan.window_id is not None, "resource load window missing")
        selected = resource_statistics(plan, sample, trace, window)
        if type(selected_resources) is CompactResources:
            selected_resources.add(selected)
        else:
            selected_resources[key] = selected
    return selected_resources


def bind_native_target(
    intent, bindings, *, sample_id, action_id, page_id, context_id, clock_id, trigger_ns
):
    """The first-issued binding is unique; neither intent nor plan is rewritten."""
    rows = [b for b in bindings if b.sample_id == sample_id]
    require(len(rows) == 1, "missing/multiple first issued bindings")
    b = rows[0]
    require(
        (b.action_id, b.page_id, b.context_id, b.clock_id)
        == (action_id, page_id, context_id, clock_id),
        "foreign response action/owner/clock",
    )
    require(
        trigger_ns <= int(b.request_ns) <= int(b.received_ns), "response predates earliest action"
    )
    require(
        b.target.scope_id == intent.scope_id
        and b.target.run_id == intent.run_id
        and b.target.step_id == intent.step_id,
        "first response foreign public scope/Run/step",
    )
    require(
        b.session_id == intent.session_id and b.batch_id == intent.batch_id,
        "first response foreign session/batch",
    )
    return b.target


def resolved_targets(plans, measurements, clock_id):
    samples = unique(measurements.samples, "sample_id")
    require(
        all(
            b.sample_id in plans and plans[b.sample_id].intent is not None
            for b in measurements.native_bindings
        ),
        "orphan native target binding",
    )
    return {
        key: plan.target
        if plan.intent is None
        else bind_native_target(
            plan.intent,
            measurements.native_bindings,
            sample_id=key,
            action_id=plan.action_id,
            page_id=plan.page_id,
            context_id=plan.context_id,
            clock_id=clock_id,
            trigger_ns=samples[key].trigger_ns,
        )
        for key, plan in plans.items()
    }


def progress_subsumption(rows, painted, *, public_step_id, public_revision, public_message):
    """Only complete same-claim committed fragment sequences may coalesce."""
    from app.application.execution.view_facts import attempt_key

    require(bool(rows) and painted in rows, "missing committed painted progress")
    claim = (painted.run_id, painted.activity_id, painted.generation, painted.claim_generation)
    require(
        public_step_id == attempt_key(*claim[1:])
        and public_revision >= painted.projection_revision
        and public_message == painted.public_message,
        "public progress is not mapped committed value",
    )
    previous = None
    for row in rows:
        require(
            (row.run_id, row.activity_id, row.generation, row.claim_generation) == claim
            and row.ack
            and row.applied
            and row.error is None
            and row.source_identity == row.event_id == row.public_event_id
            and row.source_activity_id == row.activity_id
            and row.source_generation == row.generation
            and row.source_claim_generation == row.claim_generation
            and row.source_sequence == row.sequence
            and row.public_run_id == row.run_id
            and row.public_message == row.message,
            "foreign/ineffective source progress",
        )
        require(
            row.message.startswith("Received fragments: ") and row.message[20:].isdigit(),
            "not actual fragment progress",
        )
        if previous:
            require(
                row.sequence == previous.sequence + 1
                and int(row.message[20:]) == int(previous.message[20:]) + 1
                and row.projection_revision > previous.projection_revision
                and row.observed_order > previous.observed_order,
                "missing/reordered committed subsumption",
            )
        previous = row
    require(rows[-1] == painted, "cannot subsume a future source event")
    return [r.progress_id for r in rows]


def native_clock_bounds(local_ms, calibrations):
    """Conservative mapped interval from actual dispatch/evaluation/receipt pairs.

    No midpoint is an observation. Empty/intersecting uncertainty is never hidden.
    Callers preserve raw local facts and use both bounds for fixed-slot membership.
    """
    from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

    require(bool(calibrations), "native clock calibration missing")
    value = Decimal(str(local_ms)) * 1_000_000
    lowers, uppers = [], []
    for row in calibrations:
        anchor = Decimal(str(row.local_ms)) * 1_000_000
        before, after = int(row.host_before_ns), int(row.host_after_ns)
        require(before <= after, "native calibration bracket reversed")
        lowers.append(before + int((value - anchor).to_integral_value(rounding=ROUND_FLOOR)))
        uppers.append(after + int((value - anchor).to_integral_value(rounding=ROUND_CEILING)))
    lower, upper = max(lowers), min(uppers)
    require(0 <= lower <= upper, "native clock bracket drift/disjoint")
    return lower, upper


def native_slot_membership(bounds, start, end):
    lower, upper = bounds
    require(lower <= upper and start < end, "native slot invalid")
    if start <= lower and upper < end:
        return "inside"
    if upper < start or lower >= end:
        return "outside"
    raise ValueError("native clock uncertainty crosses fixed slot boundary")


def native_action_trigger(
    record, *, sample_id, action_id, context_id, page_id, clock_id, operation, first_request_ns
):
    """Bind the observed start, including its durable-ACK overhead, for assembly."""
    require(
        (record.sample_id, record.action_id, record.context_id, record.page_id, record.clock_id)
        == (sample_id, action_id, context_id, page_id, clock_id),
        "native action owner/clock mismatch",
    )
    action = record.observation
    require(
        action.kind == "action" and action.operation == operation,
        "native action operation mismatch",
    )
    trigger = int(action.trigger_ns)
    require(
        trigger <= int(record.received_ns) <= first_request_ns,
        "native action does not precede first request",
    )
    return trigger


def validate_native_trace_completion(
    record, *, clock_id, deadline_ns, retained_bytes, retained_chunks, retained_sha256
):
    """Shared leaf predicate; callers also join exact stream/attempt/raw ownership."""
    trace = record.observation
    require(
        record.clock_id == clock_id and trace.kind == "trace-completion",
        "native trace owner/clock mismatch",
    )
    require(
        trace.data_loss is False and trace.parser == "complete" and trace.stream_id is not None,
        "native trace loss/incomplete",
    )
    times = (
        trace.end_dispatched_ns,
        trace.end_received_ns,
        trace.complete_received_ns,
        trace.eof_received_ns,
    )
    require(
        all(t is not None and int(t) <= int(record.received_ns) < deadline_ns for t in times),
        "native trace incomplete/late receipts",
    )
    require(
        trace.retained_bytes == trace.observed_bytes == retained_bytes
        and trace.retained_chunks == retained_chunks
        and trace.retained_sha256 == retained_sha256,
        "native trace retained bytes mismatch",
    )
