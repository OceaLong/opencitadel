"""Single deterministic raw-to-summary authority for producer and consumer."""

import itertools
import math
from collections import Counter, defaultdict
from datetime import datetime

from scripts.acceptance.capacity_completion import (
    COMPLETION,
    validate_completion,
    validate_resource_plan,
)
from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_package import PublicUnique
from scripts.acceptance.capacity_physical import physical, require, unique
from scripts.acceptance.capacity_public_groups import NextMarkers, PublicGroups
from scripts.acceptance.capacity_timing import validate_causal_window, validate_progress_receipts


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def dimensions():
    from scripts.acceptance.capacity import BUDGETS_MS

    return (
        {
            ("standard", mode, operation): 100 if mode == "warm" else 20
            for mode, operations in BUDGETS_MS.items()
            for operation in operations
        }
        | {
            ("step_capacity", mode, op): 100 if mode == "warm" else 20
            for mode, ops in {
                "warm": ("first_screen", "switch", "history"),
                "cold": ("first_screen", "history"),
            }.items()
            for op in ops
        }
        | {("admission", mode, "admission"): 100 for mode in ("baseline", "loaded")}
    )


def validate_windows(roles, plans):
    protocol, workload, seal, env = (
        roles[k] for k in ("protocol", "workload", "seal", "environment")
    )
    expected = unique(protocol.windows, "window_id")
    actual = unique(workload.windows, "window_id")
    planned_windows = {p.window_id for p in plans.values() if p.window_id is not None}
    require(
        len(expected) == len(actual) == len(planned_windows)
        and set(expected) == set(actual) == planned_windows,
        "window preregistration mismatch",
    )
    from scripts.acceptance.capacity_public_ids import PublicIDs

    owner = actual._rows._owner if type(actual) is PublicUnique else None
    used_runs, used_sessions, used_batches = (
        (set(), set(), set())
        if owner is None
        else tuple(
            PublicIDs(owner, name)
            for name in ("used-live-runs", "used-live-sessions", "used-live-batches")
        )
    )
    round_ids = [w.round_origin.round_id for w in workload.windows]
    require(len(set(round_ids)) == len(round_ids), "physical round reused across windows")
    progress = unique(workload.progress, "progress_id")
    unique(workload.progress, "event_id")
    require(not workload.errors, "workload errors present")
    indexed_progress = type(progress) is PublicUnique
    if indexed_progress:
        progress_by_window = PublicGroups(progress, "progress-window")
        progress_by_run = PublicGroups(progress, "progress-run")
    else:
        progress_by_window = defaultdict(list)
        for row in workload.progress:
            progress_by_window[row.window_id].append(row)
    for key, plan in expected.items():
        window = actual[key]
        require(
            window.round_origin.parent_attempt_id == protocol.attempt_id
            and window.round_origin.window_id == key
            and window.round_origin.sample_id in plans
            and plans[window.round_origin.sample_id].window_id == key,
            "parent/round/window/sample binding mismatch",
        )
        validate_causal_window(protocol, plan, window)
        require(
            len(window.context_ids) == 10 and len(set(window.context_ids)) == 10,
            "ten actual browser contexts required",
        )
        require(
            len(plan.session_ids) == 10
            and len(set(plan.session_ids)) == 10
            and window.session_ids == plan.session_ids,
            "window session preregistration mismatch",
        )
        claims = unique(window.claims, "run_id")
        require(
            len(claims) == 10 and {c.session_id for c in claims.values()} == set(plan.session_ids),
            "ten actual claims required",
        )
        require(
            not (used_runs & set(claims) if owner is None else used_runs.intersects(set(claims)))
            and not (
                used_sessions & set(plan.session_ids)
                if owner is None
                else used_sessions.intersects(set(plan.session_ids))
            )
            and window.batch_id not in used_batches,
            "window cohort/session/batch reuse",
        )
        used_runs.update(claims)
        used_sessions.update(plan.session_ids)
        used_batches.add(window.batch_id)
        require(
            window.batch_id != seal.completed_corpus_batch_id and window.batch_results == 5000,
            "completed corpus is not active load",
        )
        unique(window.claims, "call_identity")
        for claim in claims.values():
            require(
                claim.boot_id == window.boot_id
                and claim.policy_id == seal.policy_id
                and claim.stream,
                "claim boot/policy/profile mismatch",
            )
        ticks = window.ticks
        require(
            len(ticks) >= 2
            and ticks[0].after_ns <= window.guest_start_ns - protocol.load_ready_ns
            and ticks[-1].before_ns >= window.guest_end_ns,
            "batch continuity coverage missing",
        )
        for before, after in itertools.pairwise(ticks):
            require(
                before.after_ns <= after.before_ns
                and 0 < after.after_ns - before.before_ns <= 100000000
                and after.sends >= before.sends
                and after.settled >= before.settled,
                "batch continuity/dispatch gap",
            )
        for tick in ticks:
            require(tick.before_ns <= tick.after_ns, "batch snapshot bracket invalid")
            lim = env.runtime_limits
            require(
                (tick.subject_concurrency, tick.judge_concurrency, tick.environment_concurrency)
                == (lim.subject_concurrency, lim.judge_concurrency, lim.environment_concurrency),
                "batch default policy mismatch",
            )
            require(
                bool(tick.active_call_ids)
                and len(set(tick.active_call_ids)) == len(tick.active_call_ids),
                "batch has no active physical dispatch",
            )
        start_tick = min(ticks, key=lambda t: abs(t.after_ns - window.guest_start_ns))
        require(
            ticks[-1].sends > start_tick.sends and ticks[-1].settled > start_tick.settled,
            "batch did not dispatch and settle during window",
        )
        rows = progress_by_window[(key,)] if indexed_progress else progress_by_window[key]
        groups = {run: None if indexed_progress else [] for run in claims}
        for row in rows:
            require(row.run_id in groups, "foreign/replaced live Run")
            claim = claims[row.run_id]
            require(
                (row.activity_id, row.generation, row.claim_generation, row.boot_id)
                == (claim.activity_id, claim.generation, claim.claim_generation, window.boot_id),
                "progress actual claim mismatch",
            )
            require(
                0 <= row.before_ns <= row.after_ns
                and window.guest_start_ns <= row.after_ns < window.guest_end_ns,
                "progress clock/ack outside window",
            )
            require(
                row.ack and row.error is None and row.applied,
                "false/failed/stale progress acknowledgement",
            )
            require(
                (
                    row.source_identity,
                    row.source_activity_id,
                    row.source_generation,
                    row.source_claim_generation,
                    row.source_sequence,
                )
                == (
                    row.event_id,
                    row.activity_id,
                    row.generation,
                    row.claim_generation,
                    row.sequence,
                ),
                "source progress identity mismatch",
            )
            require(
                (row.public_event_id, row.public_run_id, row.public_message)
                == (row.event_id, row.run_id, row.message),
                "persisted public progress mismatch",
            )
            require(
                row.phase == plan.measurement.phase(row.after_ns - window.guest_start_ns),
                "progress immutable measurement phase differs",
            )
            if not indexed_progress:
                groups[row.run_id].append(row)
        grouped_rows = (
            (progress_by_run[(key, run)] for run in claims) if indexed_progress else groups.values()
        )
        for rows in grouped_rows:
            if not indexed_progress:
                rows.sort(key=lambda r: r.after_ns)
            require(bool(rows), "missing effective progress")
            require(
                all(
                    r.message.startswith("Received fragments: ")
                    and r.message.removeprefix("Received fragments: ").isdigit()
                    for r in rows
                ),
                "non-fragment telemetry cannot count as progress",
            )
            for a, b in itertools.pairwise(rows):
                require(
                    b.sequence == a.sequence + 1
                    and int(b.message.split(": ")[1]) == int(a.message.split(": ")[1]) + 1
                    and b.observed_order > a.observed_order
                    and b.projection_revision > a.projection_revision,
                    "omitted/duplicate source progress sequence",
                )
            bins = Counter((r.after_ns - window.guest_start_ns) // 1000000000 for r in rows)
            require(
                all(bins[i] >= 2 for i in range(plan.seconds)),
                "effective committed rate below 2 Hz",
            )
    require(all(p.window_id in actual for p in progress.values()), "orphan progress window")
    live_values = (
        r
        for row in roles["cleanup"].rounds
        for c in row.cohorts
        if c.kind == "live"
        for r in c.run_ids
    )
    live_runs = (
        set(live_values) if owner is None else PublicIDs(owner, "retained-live-runs", live_values)
    )
    require(live_runs == used_runs, "live source inventory/window mismatch")
    admitted = {
        p.target.run_id
        for p in plans.values()
        if p.operation == "admission" and p.target is not None
    }
    admission_values = (
        run
        for row in roles["cleanup"].rounds
        for c in row.cohorts
        if c.kind == "admission"
        for run in c.run_ids
    )
    retained_admissions = (
        set(admission_values)
        if owner is None
        else PublicIDs(owner, "retained-admission-runs", admission_values)
    )
    require(retained_admissions == admitted, "admission source inventory/sample mismatch")
    return actual, progress


def derive_summary(roles, binding):
    """Parse with load_artifacts first. Raises ValueError for invalid raw joins.

    Returned arrays preserve preregistered ordinal order. They contain every
    sample; no retries, trimming, best-of or replacement samples are accepted.
    """
    from scripts.acceptance.capacity import FIXTURE_COUNTS

    protocol, measurements, seal, fixture = (
        roles[k] for k in ("protocol", "measurements", "seal", "fixture")
    )
    for key, role in roles.items():
        if key != "fixture":
            require(
                (role.attempt_id, role.protocol_id) == (protocol.attempt_id, protocol.protocol_id),
                "role attempt/protocol mismatch",
            )
    require(protocol.binding_digest == canonical_digest(binding), "protocol build binding mismatch")
    require(fixture.counts == FIXTURE_COUNTS, "fixture counts mismatch")
    start, end = (
        datetime.fromisoformat(fixture.window_start),
        datetime.fromisoformat(fixture.window_end),
    )
    require(
        start.tzinfo is not None
        and end.tzinfo is not None
        and (end - start).total_seconds() == 90 * 86400,
        "fixture window must be 90 days",
    )
    require(
        seal.fixture_id == fixture.fixture_id
        and seal.fixture_manifest_digest == binding["fixture_manifest_digest"]
        and seal.migration == binding["migration"],
        "seal/fixture/build mismatch",
    )
    require(fixture.scope_ids == [fixture.target.scope_id], "fixture scope mismatch")
    plans = unique(protocol.samples, "sample_id")
    unique(protocol.samples, "action_id")
    expected = dimensions()
    counts = Counter((p.dimension, p.mode, p.operation) for p in plans.values())
    require(counts == Counter(expected), "incomplete/unknown preregistered sample dimensions")
    for group, count in expected.items():
        require(
            sorted(p.ordinal for p in plans.values() if (p.dimension, p.mode, p.operation) == group)
            == list(range(count)),
            "missing/duplicate sample ordinal",
        )
    samples = unique(measurements.samples, "sample_id")
    require(
        len(samples) == len(plans) and set(samples) == set(plans),
        "sample ledger missing/extra sample",
    )
    sources = unique(measurements.sources, "source_id")
    browsers = unique(measurements.browsers, "browser_id")
    resources = unique(measurements.resources, "sample_id")
    require(
        len(sources) == len(samples) and {s.sample_id for s in sources.values()} == set(samples),
        "source/sample cardinality mismatch",
    )
    visual = {p.sample_id for p in plans.values() if p.operation != "admission"}
    require(
        len(browsers) == len(visual)
        and {b.sample_id for b in browsers.values()} == visual
        and len(resources) == len(visual)
        and set(resources) == visual,
        "native/resource coverage missing",
    )
    windows, progress = validate_windows(roles, plans)
    window_plans = unique(protocol.windows, "window_id")
    for plan in plans.values():
        if plan.operation == "live_visible":
            validate_resource_plan(plan, window_plans[plan.window_id])
    resource_stats = validate_completion(plans, measurements, protocol.clock_id, windows)
    probe = physical(roles, plans, samples)
    arrays = {group: [None] * n for group, n in expected.items()}
    admission_profiles, admission_sessions = set(), set()
    markers = unique(measurements.markers, "marker_id")
    paints = unique(measurements.live_paints, "progress_id")
    eligible = validate_progress_receipts(
        progress, paints, roles["workload"].source_acks, windows, protocol.clock_id
    )
    used_live_progress = set()
    require(
        0 < len(markers) <= protocol.marker_count_bound,
        "host causal markers missing or exceed preregistered bound",
    )
    for marker in markers.values():
        require(marker.window_id in windows, "orphan host marker")
        window = windows[marker.window_id]
        require(
            marker.boot_id == window.boot_id
            and marker.clock_id == protocol.clock_id
            and marker.command_nonce == marker.response_nonce,
            "marker boot/clock/command acknowledgement mismatch",
        )
        require(
            protocol.registered_ns
            < marker.scheduled_ns
            <= marker.sent_ns
            <= marker.installed_ack_ns
            <= marker.completed_ns
            <= marker.sent_ns + protocol.marker_timeout_ns,
            "marker host send/ack order",
        )
    indexed_markers = type(markers) is PublicUnique
    if indexed_markers:
        markers_by_window = PublicGroups(markers, "markers-window")
        next_marker_ns = NextMarkers(markers)
    else:
        markers_by_window = defaultdict(list)
        next_marker_ns = {}
        for marker in markers.values():
            markers_by_window[marker.window_id].append(marker)
    for window in windows.values():
        ordered = (
            markers_by_window[(window.window_id,)]
            if indexed_markers
            else sorted(markers_by_window[window.window_id], key=lambda m: m.sequence)
        )
        require(
            bool(ordered) and len({m.sequence for m in ordered}) == len(ordered),
            "window marker sequence missing/duplicate",
        )
        require(
            all(
                a.sent_ns < b.sent_ns and a.guest_installed_ns < b.guest_installed_ns
                for a, b in itertools.pairwise(ordered)
            ),
            "marker chronological order",
        )
        plan = next(p for p in protocol.windows if p.window_id == window.window_id)
        require(
            len(ordered) == plan.marker_count
            and ordered[0].guest_installed_ns < window.guest_start_ns,
            "marker schedule missing or first install after window start",
        )
        for index, marker in enumerate(ordered):
            require(
                marker.sequence == index + 1
                and marker.scheduled_ns
                == window.host_metadata_received_ns + index * protocol.marker_cadence_ns,
                "adaptive/missing marker schedule",
            )
        for before, after in itertools.pairwise(ordered):
            require(before.completed_ns <= after.sent_ns, "overlapping marker commands")
            next_marker_ns[before.marker_id] = after.guest_installed_ns
    for row in progress.values():
        require(row.marker_id in markers, "progress missing pre-sink marker capture")
        marker = markers[row.marker_id]
        require(
            (marker.window_id, marker.boot_id, marker.sequence)
            == (row.window_id, row.boot_id, row.marker_sequence),
            "progress marker identity mismatch",
        )
        require(
            marker.guest_installed_ns <= row.marker_captured_ns <= row.before_ns,
            "retroactive progress marker capture",
        )
        following = next_marker_ns.get(marker.marker_id)
        require(
            following is None or row.marker_captured_ns < following,
            "progress attached stale marker",
        )
        if row.progress_id not in paints:
            continue
        paint = paints[row.progress_id]
        window = windows[row.window_id]
        require(
            marker.sent_ns
            <= min(
                paint.source_ack_received_ns,
                paint.public_readback_received_ns,
                paint.paint_received_ns,
            )
            and window.host_ready_sent_ns
            <= paint.public_readback_received_ns
            <= paint.paint_received_ns
            and (
                row.progress_id not in eligible
                or max(paint.source_ack_received_ns, paint.paint_received_ns)
                <= window.coordinator_end_ns
            ),
            "continuous live causal ordering",
        )
    from scripts.acceptance.capacity_completion import resolved_targets

    native_targets = resolved_targets(plans, measurements, protocol.clock_id)
    for plan in plans.values():
        target = native_targets[plan.sample_id]
        sample = samples[plan.sample_id]
        require(
            sample.clock_id == protocol.clock_id and sample.action_id == plan.action_id,
            "sample clock/action mismatch",
        )
        require(
            protocol.registered_ns < sample.start_ns == sample.trigger_ns <= sample.end_ns,
            "sample negative/order/earliest trigger mismatch",
        )
        require(
            sample.status == "ok" and sample.error is None,
            "failed sample retained; attempt invalid",
        )
        require(
            (plan.mode == "cold") == (plan.reset_id is not None), "cold reset association mismatch"
        )
        if plan.mode == "warm":
            require(
                plan.prewarm_completed_ns is not None
                and protocol.registered_ns < plan.prewarm_completed_ns < sample.start_ns,
                "missing explicit warm prewarm",
            )
        else:
            require(plan.prewarm_completed_ns is None, "unexpected cold/admission prewarm")
        if plan.mode == "baseline":
            require(plan.window_id is None, "baseline under combined load")
        else:
            require(plan.window_id in windows, "sample lacks combined load window")
            w = windows[plan.window_id]
            require(
                (
                    w.host_ready_sent_ns
                    if plan.operation == "live_visible"
                    else w.coordinator_start_ns
                )
                <= sample.start_ns
                <= sample.end_ns
                <= w.coordinator_end_ns,
                "sample outside fixed workload window",
            )
            if plan.operation != "admission":
                require(plan.context_id in w.context_ids, "native context absent from workload")
            if plan.reset_id is not None:
                reset = next(r for r in roles["resets"].resets if r.reset_id == plan.reset_id)
                require(reset.boot_id == w.boot_id, "cold/window guest boot mismatch")
        require(sample.source_id in sources, "missing source acknowledgement")
        source = sources[sample.source_id]
        require(
            source.sample_id == plan.sample_id
            and source.target == target
            and source.clock_id == protocol.clock_id
            and source.policy_id == seal.policy_id,
            "source action/target/clock/policy mismatch",
        )
        require(
            (
                sample.start_ns
                <= source.submitted_ns
                <= min(source.acknowledged_ns, source.readback_ns)
                and max(source.acknowledged_ns, source.readback_ns) <= sample.end_ns
            )
            if plan.operation == "live_visible"
            else (
                sample.start_ns
                <= source.submitted_ns
                <= source.acknowledged_ns
                <= source.readback_ns
                <= sample.end_ns
            ),
            "source causal acknowledgement order",
        )
        require(source.kind == COMPLETION[plan.operation][0], "wrong completion source role")
        if plan.operation == "analysis":
            require(source.captured_runs == 100000, "analysis did not capture intended 100k Runs")
        if plan.operation == "matrix":
            require(
                source.matrix_results == 5000
                and (plan.intent.batch_id if plan.intent is not None else target.public_id)
                == seal.completed_corpus_batch_id,
                "matrix not actual 5000 corpus",
            )
        if plan.operation == "admission":
            require(
                sample.browser_id is None
                and source.admission_run_id == target.run_id
                and source.admission_session_id is not None
                and source.admission_run_id is not None,
                "HTTP-only admission lacks persisted Run attachment",
            )
            require(
                source.admission_session_id not in admission_sessions, "admission session reused"
            )
            admission_sessions.add(source.admission_session_id)
            admission_profiles.add((source.profile, source.policy_id))
        else:
            require(sample.browser_id in browsers, "missing native paint")
            browser = browsers[sample.browser_id]
            require(
                (
                    browser.sample_id,
                    browser.action_id,
                    browser.page_id,
                    browser.context_id,
                    browser.clock_id,
                )
                == (
                    plan.sample_id,
                    plan.action_id,
                    plan.page_id,
                    plan.context_id,
                    protocol.clock_id,
                ),
                "browser native identity/clock mismatch",
            )
            require(
                browser.readback_target == browser.painted_target == target
                and browser.visible
                and browser.completion == COMPLETION[plan.operation][1],
                "native selected target/paint mismatch",
            )
            require(
                source.readback_ns
                <= browser.readback_observed_ns
                <= browser.paint_observed_ns
                <= sample.end_ns
                and sample.end_ns
                == (
                    max(source.acknowledged_ns, browser.paint_observed_ns)
                    if plan.operation == "live_visible"
                    else browser.paint_observed_ns
                ),
                "public readback/paint causal ordering",
            )
            if plan.operation == "live_visible":
                require(source.progress_id in progress, "live source receipt missing")
                require(
                    source.progress_id not in used_live_progress,
                    "duplicate live latency event sample",
                )
                used_live_progress.add(source.progress_id)
                row = progress[source.progress_id]
                require(
                    row.progress_id in eligible, "tail progress cannot replace a measured sample"
                )
                live_paint = paints[row.progress_id]
                require(
                    live_paint.context_id == plan.context_id,
                    "sample live context differs from continuous observation",
                )
                require(
                    (
                        source.acknowledged_ns,
                        browser.readback_observed_ns,
                        browser.paint_observed_ns,
                    )
                    == (
                        live_paint.source_ack_received_ns,
                        live_paint.public_readback_received_ns,
                        live_paint.paint_received_ns,
                    ),
                    "sample differs from continuous native live observation",
                )
                require(
                    row.window_id == plan.window_id
                    and row.run_id == target.run_id
                    and row.event_id == source.public_event_id == browser.public_event_id
                    and row.sequence == source.sequence == browser.sequence,
                    "live source/public/paint join mismatch",
                )
                require(source.marker_id == row.marker_id, "live sample marker mismatch")
                marker = markers[row.marker_id]
                require(
                    sample.start_ns == marker.sent_ns
                    and marker.installed_ack_ns <= source.acknowledged_ns,
                    "live upper bound must start at actual host marker send",
                )
        arrays[(plan.dimension, plan.mode, plan.operation)][plan.ordinal] = (
            sample.end_ns - sample.start_ns
        ) / 1e6
    require(
        admission_profiles == {("acceptance-capacity", seal.policy_id)},
        "admission baseline/loaded profile/policy mismatch",
    )
    require(not measurements.errors, "measurement errors present")
    from scripts.acceptance.capacity_compact import CompactResources, ordered_commitment
    from scripts.acceptance.capacity_physical import summarize_cohort
    from scripts.acceptance.capacity_public_ids import PublicIDs

    if type(resource_stats) is not CompactResources:
        raise ValueError("bounded public package required for compact report")
    owner = resources._rows._owner
    cleanup = roles["cleanup"]
    standard = next(c for c in seal.cohorts if c.kind == "standard")
    summary = {
        "environment": roles["environment"].model_dump(),
        "workload": {
            "windows": len(windows),
            "browsers_per_window": 10,
            "active_runs_per_window": 10,
            "effective_progress_records": len(progress),
            "rate_rule": protocol.rate_rule,
        },
        "cache": {
            "independent_cold_resets": len(roles["resets"].resets),
            "warm_prewarms": sum(p.mode == "warm" for p in plans.values()),
            "backend": protocol.backend,
        },
        "cleanup": {
            "status": cleanup.status,
            "pending": list(cleanup.pending),
            "quarantined": list(cleanup.quarantined),
            "retained_scopes": {
                "count": len(
                    scopes := PublicIDs(
                        owner, "retained-scopes", (c.scope_id for c in cleanup.cohorts)
                    )
                ),
                "ordered_sha256": scopes.sorted_digest(),
            },
            "retained_formal_events": standard.source.formal_events,
            "retained_observations": standard.source.observations,
            "cohorts": ordered_commitment(
                (summarize_cohort(c) for c in cleanup.cohorts), owner=owner
            ),
            "total": cleanup.total.model_dump(),
        },
        "warm": {},
        "cold": {},
        "latency": {},
        "heap": {
            "peak_mib": resource_stats.peak_heap / 1024**2,
            "visible_dom_rows": resource_stats.peak_dom,
        },
        "frames": resource_stats.result(),
        "step_capacity": {
            "cohort_id": probe.cohort_id,
            "scope_id": probe.scope_id,
            "run_id": probe.run_ids[0],
            "seal_id": seal.seal_id,
            "parity_digest": probe.parity_digest,
            "visible_steps": probe.source.visible_steps,
            "formal_events": probe.source.formal_events,
            "observations": probe.source.observations,
            "warm": {},
            "cold": {},
        },
        "errors": [],
    }
    for (dimension, mode, op), values in arrays.items():
        if dimension == "admission":
            summary["latency"][f"admission_{mode}_ms"] = values
        elif dimension == "step_capacity":
            summary["step_capacity"][mode][op] = values
        else:
            summary[mode][op] = values
    return summary


def budget_errors(summary):
    from scripts.acceptance.capacity import BUDGETS_MS

    errors = []
    for prefix, obj in (("", summary), ("step_capacity.", summary["step_capacity"])):
        for mode in ("warm", "cold"):
            for op, values in obj[mode].items():
                p95 = percentile(values, 0.95)
                limit = BUDGETS_MS[mode][op]
                if p95 > limit:
                    errors.append(f"{prefix}{mode}.{op} p95 {p95} exceeds {limit}ms")
    baseline, loaded = (
        percentile(summary["latency"][f"admission_{mode}_ms"], 0.95)
        for mode in ("baseline", "loaded")
    )
    if baseline <= 0 or loaded > baseline * 1.2:
        errors.append("admission p95 increase exceeds 20%")
    for key in ("peak_mib", "visible_dom_rows"):
        if summary["heap"][key] > 200:
            errors.append(f"invalid heap/DOM budget {key}")  # noqa: PERF401 - explicit two-budget diagnostics
    # Global worst per-target p95 is checked by validate_report in addition to pooled p95.
    if summary["frames"]["p95_ms"] is None or summary["frames"]["p95_ms"] > 33:
        errors.append("frame p95 exceeds 33ms")
    if summary["frames"]["long_tasks"]["over_200ms_count"] >= 2:
        errors.append("invalid or repeated >200ms long tasks")
    return errors
