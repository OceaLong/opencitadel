"""Discrete same-path calibration evidence. Never invent continuous coverage."""

from collections import defaultdict
from statistics import median

from scripts.acceptance.capacity_physical import require


def validate_calibrations(network, windows, clock_id, window_plans):
    from scripts.acceptance.capacity_package import PublicRows
    from scripts.acceptance.capacity_public_groups import PublicGroups
    from scripts.acceptance.capacity_public_lookup import PublicLookup

    indexed = type(network.calibrations) is PublicRows
    groups = None if indexed else defaultdict(list)
    for c in network.calibrations:
        require(
            c.window_id in windows and c.clock_id == clock_id, "calibration clock/window mismatch"
        )
        require(
            c.start_ns < c.end_ns
            and c.elapsed_ns == c.end_ns - c.start_ns
            and abs(c.bits_per_second - c.bytes * 8e9 / c.elapsed_ns) < 0.01,
            "calibration measured bytes/interval mismatch",
        )
        require(
            (c.action == "echo") == (c.echo_rtt_ns is not None)
            and (c.echo_rtt_ns is None or c.echo_rtt_ns <= c.elapsed_ns),
            "calibration actual echo interval missing",
        )
        if not indexed:
            groups[c.window_id, c.phase].append(c)
    if indexed:
        groups = PublicGroups(network.calibrations, "calibrations")
    phases = ("baseline", "pre", "window", "post")
    require(
        len(groups) == len(windows) * len(phases)
        and set(groups) == {(w, phase) for w in windows for phase in phases},
        "missing discrete calibration phase",
    )
    intervals = (
        PublicLookup(network.phase_intervals, "calibration-phase")
        if indexed
        else {(r.window_id, r.phase): r for r in network.phase_intervals}
    )
    require(
        len(intervals) == len(network.phase_intervals) and set(intervals) == set(groups),
        "missing/duplicate actual calibration phase intervals",
    )
    require(set(window_plans) == set(windows), "calibration immutable window plans differ")
    rule = network.validity
    require(
        rule.added_rtt_min_ns <= rule.added_rtt_max_ns
        and rule.throughput_min_bps <= rule.throughput_max_bps,
        "calibration validity rule invalid",
    )
    for wid, window in windows.items():
        native = None
        previous = None
        for phase in phases:
            rows = (
                groups[wid, phase]
                if indexed
                else sorted(groups[wid, phase], key=lambda c: c.ordinal)
            )
            require(
                len(rows) == 18
                and [r.ordinal for r in rows] == list(range(18))
                and [r.action for r in rows] == ["echo"] * 16 + ["upload", "download"],
                "incomplete calibration probes",
            )
            interval = intervals[wid, phase]
            slot = window_plans[wid].calibration_window
            require(
                interval.clock_id == clock_id
                and interval.scheduled_ns
                <= interval.before_ns
                < interval.after_ns
                <= interval.deadline_ns
                == interval.scheduled_ns + slot.budget_ns,
                "calibration full phase exceeded fixed deadline/clock",
            )
            require(
                interval.before_ns <= rows[0].start_ns and rows[-1].end_ns <= interval.after_ns,
                "calibration probes outside actual phase interval",
            )
            if phase == "window":
                require(
                    interval.scheduled_ns == window.coordinator_start_ns + slot.offset_ns
                    and interval.after_ns <= window.coordinator_end_ns,
                    "calibration window moved from first open/fixed slot",
                )
            elif phase in ("baseline", "pre"):
                require(
                    interval.after_ns <= window.coordinator_start_ns,
                    "calibration actual pre phase is late",
                )
            else:
                require(
                    interval.before_ns >= window.coordinator_end_ns,
                    "calibration actual post phase is early",
                )
            for r in rows:
                require(
                    previous is None or previous <= r.start_ns, "overlapping calibration probes"
                )
                previous = r.end_ns
            if phase in ("baseline", "pre"):
                require(
                    rows[-1].end_ns <= window.coordinator_start_ns, "calibration pre phase is late"
                )
            if phase == "window":
                require(
                    window.coordinator_start_ns <= rows[0].start_ns
                    and rows[-1].end_ns <= window.coordinator_end_ns,
                    "calibration window probes lack covered interval",
                )
            if phase == "post":
                require(
                    rows[0].start_ns >= window.coordinator_end_ns, "calibration post phase is early"
                )
            rtt = median(r.echo_rtt_ns for r in rows if r.action == "echo")
            if phase == "baseline":
                native = rtt
            else:
                require(
                    rule.added_rtt_min_ns <= rtt - native <= rule.added_rtt_max_ns,
                    "calibration observed added RTT outside preregistration",
                )
                require(
                    all(
                        rule.throughput_min_bps <= r.bits_per_second <= rule.throughput_max_bps
                        for r in rows
                        if r.action != "echo"
                    ),
                    "calibration throughput outside preregistration",
                )
