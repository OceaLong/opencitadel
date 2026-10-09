"""Shared causal timing joins for actual source/SQL/host observations."""

from datetime import datetime
from itertools import pairwise

from scripts.acceptance.capacity_physical import require, unique


def wall(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "SQL wall-clock timezone missing")
    return result


def claim_key(row):
    return row.run_id, row.activity_id, row.generation, row.claim_generation, row.call_identity


def validate_snapshots(window, *, load_ready_ns):
    snapshots = window.snapshots
    expected = {claim_key(c) for c in window.claims}
    require(
        bool(snapshots)
        and snapshots[0].after_ns <= window.guest_start_ns - load_ready_ns
        and snapshots[-1].before_ns >= window.guest_end_ns,
        "claim snapshot coverage missing",
    )
    previous = {}
    for snapshot in snapshots:
        require(
            snapshot.boot_id == window.boot_id and snapshot.before_ns <= snapshot.after_ns,
            "claim snapshot boot/bracket mismatch",
        )
        unique(snapshot.claims, "call_identity")
        require(
            {claim_key(c) for c in snapshot.claims} == expected,
            "claim/task/Run/reservation continuity mismatch",
        )
        for c in snapshot.claims:
            observed, started, heartbeat, deadline, timeout = map(
                wall,
                (
                    c.sql_observed_at,
                    c.call_started_at,
                    c.heartbeat_at,
                    c.claim_deadline,
                    c.timeout_at,
                ),
            )
            require(
                c.reservation_id == c.call_identity
                and c.lease_live
                and not c.settled
                and not c.terminal
                and c.status == "call_started"
                and c.reservation_state == "dispatching"
                and c.stream
                and c.configured_model == "acceptance-live"
                and started <= observed
                and heartbeat <= observed < min(deadline, timeout),
                "claim lease/terminal/reservation SQL observation invalid",
            )
            require(
                c.policy_id
                == next(
                    claim.policy_id
                    for claim in window.claims
                    if claim.call_identity == c.call_identity
                ),
                "actual claim policy changed",
            )
            identity = (c.reservation_id, c.claimed_by, c.call_started_at)
            require(
                c.call_identity not in previous or previous[c.call_identity] == identity,
                "claim owner/start/reservation changed",
            )
            previous[c.call_identity] = identity
    for a, b in pairwise(snapshots):
        require(
            a.after_ns <= b.before_ns and 0 < b.after_ns - a.before_ns <= 100_000_000,
            "claim snapshot continuity gap",
        )


def validate_causal_window(protocol, plan, window):
    require(
        window.clock_id == protocol.clock_id and plan.startup_ns == protocol.startup_ns,
        "window host clock/relative startup mismatch",
    )
    require(
        window.guest_start_ns == window.guest_minimal_ready_ns + plan.startup_ns
        and window.guest_end_ns == window.guest_start_ns + plan.seconds * 1_000_000_000,
        "guest relative fixed schedule mismatch",
    )
    require(
        window.guest_minimal_ready_ns
        <= window.guest_cohort_ns
        <= window.guest_start_ns - protocol.load_ready_ns
        and window.guest_cohort_ns <= window.guest_ready_ns < window.guest_start_ns
        and window.guest_start_ns <= window.guest_done_ns <= window.guest_end_ns,
        "guest readiness/completion outside original window",
    )
    require(
        protocol.registered_ns
        < window.host_metadata_received_ns
        <= window.host_cohort_received_ns
        <= window.host_ready_sent_ns
        < window.coordinator_start_ns
        <= window.coordinator_end_ns
        <= window.host_done_sent_ns
        <= window.host_done_received_ns,
        "host causal open/native completion/done order invalid",
    )
    require(
        window.guest_start_ns + plan.measurement.end_offset_ns
        <= window.guest_measurement_closed_ns
        <= window.guest_done_ns
        and window.host_measurement_closed_received_ns <= window.coordinator_end_ns,
        "measurement obligations completed before actual interval closure",
    )
    validate_snapshots(window, load_ready_ns=protocol.load_ready_ns)


def validate_progress_receipts(progress, paints, acknowledgements, windows, clock_id):
    """Measurement eligibility never changes full-window source/rate coverage."""
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.acceptance.capacity_package import PublicUnique
    from scripts.acceptance.capacity_public_ids import PublicIDs

    if type(progress) is PublicUnique:
        owner = progress._rows._owner
        eligible = PublicIDs(
            owner,
            "eligible-progress",
            (p.progress_id for p in progress.values() if p.phase == "measured"),
        )
        paint_ids = PublicIDs(owner, "paint-progress", paints)
        progress_ids = PublicIDs(owner, "all-progress", progress)
    else:
        eligible = {p.progress_id for p in progress.values() if p.phase == "measured"}
        paint_ids, progress_ids = set(paints), set(progress)
    require(
        eligible <= paint_ids <= progress_ids,
        "every effective measured progress requires native paint",
    )
    acks = unique(acknowledgements, "progress_id")
    ack_ids = (
        PublicIDs(owner, "ack-progress", acks) if type(progress) is PublicUnique else set(acks)
    )
    require(
        eligible | paint_ids <= ack_ids <= progress_ids,
        "measured progress missing incremental receipt",
    )
    for key, ack in acks.items():
        row = progress[key]
        require(
            row.ack
            and row.error is None
            and row.applied
            and ack.clock_id == clock_id
            and ack.progress_digest == canonical_digest(row.model_dump())
            and row.after_ns <= ack.query_before_ns <= ack.query_after_ns,
            "incremental receipt/source SQL identity mismatch",
        )
    for key, paint in paints.items():
        row, window = progress[key], windows[progress[key].window_id]
        require(
            (
                paint.marker_id,
                paint.clock_id,
                paint.event_id,
                paint.run_id,
                paint.sequence,
                paint.projection_revision,
            )
            == (
                row.marker_id,
                clock_id,
                row.event_id,
                row.run_id,
                row.sequence,
                row.projection_revision,
            ),
            "continuous live source/public/paint identity mismatch",
        )
        require(
            paint.context_id
            == window.context_ids[
                window.session_ids.index(
                    next(c.session_id for c in window.claims if c.run_id == row.run_id)
                )
            ]
            and paint.visible,
            "continuous live browser not visible",
        )
        require(
            paint.source_ack_received_ns == acks[key].received_ns,
            "native source acknowledgement differs from actual incremental receipt",
        )
        require(
            window.host_ready_sent_ns
            <= paint.public_readback_received_ns
            <= paint.paint_received_ns,
            "live observer causal readiness/paint order",
        )
        if key in eligible:
            require(
                paint.source_ack_received_ns == acks[key].received_ns
                and max(paint.source_ack_received_ns, paint.paint_received_ns)
                <= window.coordinator_end_ns,
                "measured incremental receipt/paint missed client-done",
            )
    return eligible
