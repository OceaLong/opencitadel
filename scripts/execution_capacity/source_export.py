"""Allowlisted actual guest journal to shared typed source shards.

No host times, native observations, cleanup success or missing SQL facts are
manufactured. This is input to the later coordinator's Workload construction.
"""

from scripts.acceptance.capacity_models import (
    BatchTick,
    Claim,
    ClaimObservation,
    ClaimSnapshot,
    GuestWindowSource,
    Progress,
    SourceShard,
)
from scripts.execution_capacity.attempt import digest, encode

PAGE_ROWS = 16
MAX_ROWS = 10000


def export_progress(row, *, window_id, phase):
    direct = {
        key: row[key]
        for key in (
            "run_id",
            "activity_id",
            "generation",
            "claim_generation",
            "boot_id",
            "pid",
            "marker_id",
            "marker_sequence",
            "marker_captured_ns",
            "event_id",
            "sequence",
            "before_ns",
            "after_ns",
            "ack",
            "error",
            "message",
            "source_identity",
            "applied",
            "observed_order",
            "projection_revision",
            "public_event_id",
            "public_run_id",
            "public_message",
        )
    }
    return Progress.model_validate(
        {
            **direct,
            "phase": phase,
            "progress_id": row["event_id"],
            "window_id": window_id,
            **{
                "source_" + key: row["source"][key]
                for key in ("activity_id", "generation", "claim_generation", "sequence")
            },
        }
    )


def _observations(journal, kind, window_id):
    rows = []
    for identity, record in journal.records(kind):
        if record["body"]["window_id"] == window_id:
            rows.append((identity, record["body"]))
            if len(rows) > MAX_ROWS:
                raise ValueError("source observation count exceeds bound; retained")
    return sorted(rows, key=lambda r: r[1]["before_ns"])


def export_source(state, journal):
    """Extract only settled, original fixed-window source facts. No replacement."""
    state.verify()
    cohort, ready, done, settled = (
        state.read(k) for k in ("cohort", "client_ready", "client_done", "source_settled")
    )
    result = state.read("source_result")
    if any(x is None for x in (cohort, ready, done, settled, result)):
        raise ValueError("actual settled source records missing")
    if state.read("failure") or state.read("shutdown_failure"):
        raise ValueError("failed source retains private records; no successful export")
    if settled["host_physical_cleanup"] != "pending_C" or result["receipt"] is None:
        raise ValueError("source settlement/receipt schema mismatch")
    wid, boot = state.identity["window_id"], state.identity["boot_id"]
    snapshots = _observations(journal, "live_claim_snapshot", wid)
    ids = [identity for identity, _ in snapshots]
    if cohort["snapshot_id"] not in ids:
        raise ValueError("actual cohort snapshot join missing")
    # Only the fixed first successful cohort starts continuity. Earlier setup
    # attempts remain in the private journal and are never measurement samples.
    snapshots = snapshots[ids.index(cohort["snapshot_id"]) :]
    ticks = _observations(journal, "live_batch_snapshot", wid)
    ids = [identity for identity, _ in ticks]
    if cohort["snapshot_id"] not in ids:
        raise ValueError("actual cohort batch join missing")
    ticks = ticks[ids.index(cohort["snapshot_id"]) :]
    claims = [
        ClaimObservation.model_validate({key: row[key] for key in ClaimObservation.model_fields})
        for row in snapshots[0][1]["rows"]
    ]
    sessions = cohort["sessions"]
    run_sessions = {run: session for session, run in sessions.items()}
    if len(run_sessions) != len(sessions) or len(sessions) != 10:
        raise ValueError("actual ten-session cohort missing")
    expected = {
        (r["run_id"], r["activity_id"], r["generation"], r["claim_generation"], r["call_identity"])
        for r in cohort["claims"]
    }
    if {
        (c.run_id, c.activity_id, c.generation, c.claim_generation, c.call_identity) for c in claims
    } != expected:
        raise ValueError("actual cohort/claim snapshot differs")
    metadata = GuestWindowSource.model_validate(
        {
            "attempt_id": state.identity["attempt_id"],
            "measurement": state.read("measurement")["rule"],
            "window_id": wid,
            "boot_id": boot,
            "source_digest": state.identity["source_digest"],
            "minimal_ready_ns": state.read("minimal_ready")["guest_ns"],
            "start_ns": result["body"]["start_ns"],
            "end_ns": result["body"]["end_ns"],
            "cohort_ns": cohort["established_ns"],
            "ready_ns": ready["guest_ns"],
            "done_ns": done["guest_ns"],
            "measurement_closed_ns": state.read("measurement_closed")["guest_ns"],
            "cohort_digest": cohort["cohort_digest"],
            "native_ready_digest": ready["native_digest"],
            "native_done_digest": done["native_digest"],
            "session_ids": list(sessions),
            "claims": [
                Claim.model_validate(
                    {
                        **{
                            key: getattr(c, key)
                            for key in (
                                "run_id",
                                "activity_id",
                                "generation",
                                "claim_generation",
                                "call_identity",
                                "policy_id",
                                "configured_model",
                                "stream",
                            )
                        },
                        "session_id": run_sessions[c.run_id],
                        "boot_id": boot,
                    }
                )
                for c in claims
            ],
            "batch_id": cohort["batch_id"],
            "suite_version": ticks[0][1]["suite_version"],
            # Produced by start_batch's validated real suite, retained below.
            "batch_results": journal.parent("live_batch", cohort["batch_id"])["batch_results"],
            "cleanup": settled["host_physical_cleanup"],
        }
    )
    exported_snapshots = [
        ClaimSnapshot.model_validate(
            {
                "before_ns": row["before_ns"],
                "after_ns": row["after_ns"],
                "boot_id": row["boot_id"],
                "claims": [
                    {key: c[key] for key in ClaimObservation.model_fields} for c in row["rows"]
                ],
            }
        )
        for _, row in snapshots
    ]
    exported_ticks = []
    for _, row in ticks:
        if (
            row["batch_id"] != metadata.batch_id
            or row["suite_version"] != metadata.suite_version
            or row["boot_id"] != boot
        ):
            raise ValueError("batch snapshot identity changed")
        batch = row["batch"]
        exported_ticks.append(
            BatchTick.model_validate(
                {
                    "before_ns": row["before_ns"],
                    "after_ns": row["after_ns"],
                    "status": batch["status"],
                    **{
                        key: batch["settings"][key]
                        for key in (
                            "subject_concurrency",
                            "judge_concurrency",
                            "environment_concurrency",
                        )
                    },
                    **{key: batch[key] for key in ("sends", "settled", "active_call_ids")},
                }
            )
        )
    return {
        "metadata": [metadata],
        "snapshots": exported_snapshots,
        "ticks": exported_ticks,
        "progress": final_progress(state, result["receipt"]["updates"]),
    }


def source_shard(state, journal, kind, index):
    if (
        kind not in {"metadata", "snapshots", "ticks", "progress"}
        or type(index) is not int
        or index < 0
    ):
        raise ValueError("fixed source shard kind/index required")
    records = export_source(state, journal)[kind]
    if not records or len(records) > MAX_ROWS:
        raise ValueError("missing/oversize actual source records")
    count = (len(records) + PAGE_ROWS - 1) // PAGE_ROWS
    if index >= count:
        raise ValueError("source shard outside recorded count")
    rows = [row.model_dump() for row in records[index * PAGE_ROWS : (index + 1) * PAGE_ROWS]]
    if len(encode(rows)) > 512 * 1024:
        raise ValueError("source shard byte bound; retained")
    return SourceShard.model_validate(
        {
            "window_id": state.identity["window_id"],
            "boot_id": state.identity["boot_id"],
            "kind": kind,
            "index": index,
            "count": count,
            "total": len(records),
            "rows": rows,
            "digest": digest(rows),
        }
    )


def publish_progress(state, rows, measurement, *, start_ns, query_before_ns, query_after_ns):
    """Append only actual successful sink receipts independently SQL/public joined.

    Single long source owns writing; short feed readers never query business data.
    All source errors remain in the original journal and invalidate final export.
    """
    from scripts.acceptance.capacity_models import JoinedProgress

    state.verify()
    if query_before_ns > query_after_ns:
        raise ValueError("invalid SQL query bracket")
    for raw in rows:
        if (
            raw.get("ack") is not True
            or raw.get("error") is not None
            or raw.get("effective") is not True
            or raw.get("public") is not True
            or raw.get("after_ns") is None
            or raw["after_ns"] > query_before_ns
        ):
            raise ValueError("incremental acknowledgement requires persisted SQL/public join")
        progress = export_progress(
            raw,
            window_id=state.identity["window_id"],
            phase=measurement.phase(raw["after_ns"] - start_ns),
        )
        if (
            progress.boot_id != state.identity["boot_id"]
            or not progress.applied
            or progress.source_identity != progress.event_id
            or (
                progress.source_activity_id,
                progress.source_generation,
                progress.source_claim_generation,
                progress.source_sequence,
            )
            != (
                progress.activity_id,
                progress.generation,
                progress.claim_generation,
                progress.sequence,
            )
            or (progress.public_event_id, progress.public_run_id, progress.public_message)
            != (progress.event_id, progress.run_id, progress.message)
        ):
            raise ValueError("persisted source/public identity differs")
        joined = JoinedProgress(
            progress=progress, query_before_ns=query_before_ns, query_after_ns=query_after_ns
        )
        db = state.journal.db
        db.execute("BEGIN IMMEDIATE")
        try:
            entries = db.execute(
                "SELECT identity,body FROM intents WHERE kind='bridge_progress' "
                "AND json_extract(body,'$.progress.window_id')=? ORDER BY identity",
                (state.identity["window_id"],),
            ).fetchall()
            import json

            previous = [json.loads(body) for _, body in entries]
            old = [v for v in previous if v["progress"]["progress_id"] == progress.progress_id]
            if old:
                if old[0]["progress"] != progress.model_dump():
                    raise ValueError("immutable incremental source changed")
            else:
                if len(entries) >= MAX_ROWS:
                    raise ValueError("incremental source bound exceeded; retained")
                db.execute(
                    "INSERT INTO intents(kind,identity,body) VALUES('bridge_progress',?,?)",
                    (
                        state.identity["window_id"] + ":" + str(len(entries)).zfill(5),
                        encode(joined.model_dump()).decode(),
                    ),
                )
            db.commit()
        except BaseException:
            db.rollback()
            raise


def progress_page(state, cursor):
    """Read one consistent bounded prefix of the durable feed, including tail."""
    import json

    from scripts.acceptance.capacity_models import ProgressPage

    state.verify()
    if type(cursor) is not int or not 0 <= cursor <= MAX_ROWS:
        raise ValueError("bounded incremental cursor required")
    db = state.journal.db
    db.execute("BEGIN")
    try:
        args = (state.identity["window_id"],)
        where = "kind='bridge_progress' AND json_extract(body,'$.progress.window_id')=?"
        total = db.execute("SELECT count(*) FROM intents WHERE " + where, args).fetchone()[0]
        if cursor > total or total > MAX_ROWS:
            raise ValueError("incremental cursor/count differs")
        rows = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM intents WHERE " + where + " ORDER BY identity LIMIT ? OFFSET ?",
                (*args, PAGE_ROWS, cursor),
            )
        ]
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if len(encode(rows)) > 512 * 1024:
        raise ValueError("incremental page byte bound exceeded")
    return ProgressPage.model_validate(
        {
            "window_id": state.identity["window_id"],
            "boot_id": state.identity["boot_id"],
            "cursor": cursor,
            "next_cursor": cursor + len(rows),
            "total": total,
            "rows": rows,
            "digest": digest(rows),
        }
    )


def final_progress(state, rows):
    from scripts.acceptance.capacity_models import MeasurementInterval

    observed = state.read("measurement")
    if observed is None:
        raise ValueError("actual immutable measurement rule missing")
    measurement = MeasurementInterval.model_validate(observed["rule"])
    final = [
        export_progress(
            row,
            window_id=state.identity["window_id"],
            phase=measurement.phase(row["after_ns"] - observed["start_ns"]),
        )
        for row in rows
    ]
    final_by_id = {row.progress_id: row for row in final}
    cursor = 0
    while True:
        page = progress_page(state, cursor)
        for joined in page.rows:
            progress = joined.progress
            if (
                observed["start_ns"] <= progress.after_ns < observed["end_ns"]
                and final_by_id.get(progress.progress_id) != progress
            ):
                raise ValueError("incremental/final source facts differ")
        cursor = page.next_cursor
        if cursor == page.total:
            break
    return final
