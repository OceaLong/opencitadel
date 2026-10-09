"""Actual child source/control receipts to shared parent-report window facts."""

from scripts.acceptance.capacity_models import GuestWindowSource, SourceAck, Window
from scripts.execution_capacity.attempt import digest


def exact(rows, label):
    if len(rows) != 1:
        raise ValueError("exact actual " + label + " observation required")
    return rows[0]


def observed(ledger, identity, kind):
    return exact(
        [r["body"] for r in ledger.records(kind) if r["body"].get("identity") == identity], kind
    )


def command_interval(ledger, identity, action):
    command = exact(
        [
            r["body"]
            for r in ledger.records("guest-control-intent")
            if r["body"]["action"] == action and r["body"]["request"]["identity"] == identity
        ],
        action,
    )
    receipts = [
        r
        for r in ledger.records("guest-control-result")
        if r["body"]["command_id"] == command["command_id"]
    ]
    receipts += [
        r
        for r in ledger.records("guest-status-poll-outcome")
        if r["body"]["owner"] == "short"
        and r["body"]["state"] == "terminal-valid"
        and r["body"]["command_id"] == command["command_id"]
    ]
    if not receipts:
        raise ValueError("actual command receipt missing")
    return command["host_ns"], min(receipts, key=lambda r: r["sequence"])["body"]["host_ns"]


def incremental_receipts(ledger, identity, clock_id):
    progress, acknowledgements = {}, {}
    for record in ledger.records("guest-progress-page"):
        row = record["body"]
        if row["identity"] != identity:
            continue
        for joined in row["page"]["rows"]:
            value = joined["progress"]
            key = value["progress_id"]
            if key in progress:
                raise ValueError("duplicate incremental progress event")
            progress[key] = value
            acknowledgements[key] = SourceAck(
                progress_id=key,
                clock_id=clock_id,
                received_ns=row["host_ns"],
                progress_digest=digest(value),
                query_before_ns=joined["query_before_ns"],
                query_after_ns=joined["query_after_ns"],
            )
    return progress, acknowledgements


def join_window(ledger, identity, source, snapshots, ticks, contexts, binding, clock_id):
    source = GuestWindowSource.model_validate(source)
    if (
        source.attempt_id != binding["round_id"]
        or identity["attempt_id"] != binding["round_id"]
        or source.window_id != binding["window_id"]
        or source.boot_id != identity["boot_id"]
    ):
        raise ValueError("actual parent/round/guest source binding mismatch")
    ready_sent, _ = command_interval(ledger, identity, "client-ready")
    done_sent, done_received = command_interval(ledger, identity, "client-done")
    return Window(
        round_origin=binding,
        window_id=source.window_id,
        boot_id=source.boot_id,
        guest_minimal_ready_ns=source.minimal_ready_ns,
        guest_start_ns=source.start_ns,
        guest_end_ns=source.end_ns,
        guest_cohort_ns=source.cohort_ns,
        guest_ready_ns=source.ready_ns,
        guest_done_ns=source.done_ns,
        guest_measurement_closed_ns=source.measurement_closed_ns,
        host_measurement_closed_received_ns=observed(
            ledger, identity, "guest-measurement-closed-received"
        )["host_ns"],
        host_metadata_received_ns=observed(ledger, identity, "guest-metadata-anchor")["host_ns"],
        host_cohort_received_ns=observed(ledger, identity, "guest-cohort-received")["host_ns"],
        host_ready_sent_ns=ready_sent,
        host_done_sent_ns=done_sent,
        host_done_received_ns=done_received,
        native_ready_digest=source.native_ready_digest,
        native_done_digest=source.native_done_digest,
        snapshots=snapshots,
        ticks=ticks,
        clock_id=clock_id,
        context_ids=contexts,
        coordinator_start_ns=observed(ledger, identity, "guest-window-open-received")["host_ns"],
        coordinator_end_ns=observed(ledger, identity, "native-complete-observed")["host_ns"],
        session_ids=source.session_ids,
        claims=source.claims,
        batch_id=source.batch_id,
        suite_version=source.suite_version,
        batch_results=source.batch_results,
    )


def calibration_intervals(ledger, window_id, clock_id):
    from scripts.acceptance.capacity_models import CalibrationPhaseInterval

    rows = [
        CalibrationPhaseInterval.model_validate(r["body"])
        for r in ledger.records("calibration-phase-interval")
        if r["body"]["window_id"] == window_id
    ]
    if len(rows) != 4 or {r.phase for r in rows} != {"baseline", "pre", "window", "post"}:
        raise ValueError("actual four calibration phase intervals required")
    if any(r.clock_id != clock_id for r in rows):
        raise ValueError("actual calibration phase clock differs")
    return rows
