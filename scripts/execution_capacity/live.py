"""Live source evidence, separate from native paint/benchmark acceptance (C)."""

import json
import logging
import os
import time
from itertools import pairwise
from pathlib import Path
from uuid import uuid4


def validate_topology(settings, workers):
    """Check existing settings; this never activates or increases any limit."""
    required = (
        10 + settings.evaluation_subject_concurrency + settings.evaluation_judge_concurrency + 1
    )
    concurrency = settings.execution_activity_max_concurrency
    if (
        type(workers) is not int
        or not 1 <= workers <= 16
        or workers * concurrency < required
        or not 1 <= settings.execution_activity_batch_size <= concurrency
        or concurrency > settings.postgres_pool_size + settings.postgres_max_overflow
    ):
        raise ValueError("live worker/pool/claim headroom unavailable")
    for name in ("global", "user", "provider"):
        cap = getattr(settings, f"physical_{name}_concurrency")
        if cap is not None and cap < required:
            raise ValueError("existing physical policy lacks live headroom")
    return {
        "workers": workers,
        "required_handlers": required,
        "per_worker": concurrency,
        "claim_batch_size": settings.execution_activity_batch_size,
        "pool_size": settings.postgres_pool_size,
        "max_overflow": settings.postgres_max_overflow,
        "subject_concurrency": settings.evaluation_subject_concurrency,
        "judge_concurrency": settings.evaluation_judge_concurrency,
        "environment_concurrency": settings.evaluation_environment_concurrency,
    }


def progress_records(journal, runs):
    """Bound local observer reads to this finite cohort, not previous rounds."""
    if not runs:
        return []
    journal.db.execute(
        "CREATE INDEX IF NOT EXISTS live_progress_run ON intents(kind,json_extract(body,'$.run_id'))"
    )
    placeholders = ",".join("?" for _ in runs)
    rows = journal.db.execute(
        "SELECT body,receipt FROM intents WHERE kind='live_progress' AND json_extract(body,'$.run_id') IN ("
        + placeholders
        + ")",
        list(runs),
    ).fetchall()
    return [
        {"body": json.loads(body), "receipt": json.loads(receipt) if receipt else None}
        for body, receipt in rows
    ]


class ObservedProgress:
    """Pass-through only: workers call the real sink, no workload is manufactured.

    Journal failures cannot change activity semantics. Missing receipts invalidate
    the attempt when read back. Every process must share the same Linux boot ID;
    no subtraction across host/browser clock epochs is allowed.
    """

    def __init__(self, delegate, journal, *, boot_id=None, marker_root=None):
        self.delegate, self.journal = delegate, journal
        self.marker_root = marker_root
        self.boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    async def record(self, record):
        identity = str(uuid4())
        body = {
            **record.model_dump(mode="json"),
            "event_id": str(record.event_id),
            "boot_id": self.boot_id,
            "pid": os.getpid(),
            "before_ns": time.monotonic_ns(),
        }
        if self.marker_root is not None:
            try:
                from scripts.execution_capacity.guest_state import read_marker

                marker = read_marker(self.marker_root, boot_id=self.boot_id)
                body.update(
                    marker_id=marker["marker_id"],
                    marker_sequence=marker["sequence"],
                    marker_window_id=marker["window_id"],
                    marker_captured_ns=time.monotonic_ns(),
                )
                body["before_ns"] = time.monotonic_ns()
            except Exception as failure:  # noqa: BLE001 - retain evidence failure, preserve sink
                body["marker_error"] = type(failure).__name__
        try:
            self.journal.intent("live_progress", identity, body)
        except Exception:  # noqa: BLE001 - observation must not change display-only semantics
            logging.getLogger(__name__).error("live progress intent unavailable")
        try:
            ack = await self.delegate.record(record)
        except BaseException as error:
            self._ack(
                identity,
                {"ack": False, "after_ns": time.monotonic_ns(), "error": type(error).__name__},
            )
            raise
        self._ack(identity, {"ack": ack, "after_ns": time.monotonic_ns(), "error": None})
        return ack

    def _ack(self, identity, receipt):
        try:
            self.journal.acknowledge("live_progress", identity, receipt)
        except Exception:  # noqa: BLE001 - observation must not change display-only semantics
            logging.getLogger(__name__).error("live progress receipt unavailable")


def validate_updates(rows, runs, start_ns, end_ns):
    """Preregistered whole-second bins: >=2 distinct effective updates/Run/bin.

    No rate tolerance, replacement participant, duplicate ack, failed update or
    final phase marker counts. All unsuccessful submissions within window fail.
    Public here means committed authorized event feed, NOT DOM paint.
    """
    duration = end_ns - start_ns
    if (
        len(runs) != 10
        or len(set(runs)) != 10
        or duration < 2_000_000_000
        or duration % 1_000_000_000
    ):
        raise ValueError("live window requires ten fixed Runs and whole seconds >=2")
    bins = duration // 1_000_000_000
    groups = {run: [] for run in runs}
    identities = set()
    for row in rows:
        if row["run_id"] not in groups:
            raise ValueError("live cohort replacement/foreign update")
        if not (0 <= row["before_ns"] <= row["after_ns"] and start_ns <= row["after_ns"] < end_ns):
            raise ValueError("progress submission/ack lies outside bounded window")
        if row["ack"] is not True or row["effective"] is not True or row["public"] is not True:
            raise ValueError("failed/ineffective/nonpublic live update")
        if row["event_id"] in identities:
            raise ValueError("duplicate live acknowledgement")
        identities.add(row["event_id"])
        groups[row["run_id"]].append(row)
    for updates in groups.values():
        updates.sort(key=lambda r: r["after_ns"])
        claims = {(r["activity_id"], r["generation"], r["claim_generation"]) for r in updates}
        if len(claims) != 1:
            raise ValueError("lost/replaced active handler")
        if any(b["sequence"] != a["sequence"] + 1 for a, b in pairwise(updates)):
            raise ValueError("omitted/duplicate live sequence")
        counts = [
            int(r["message"].removeprefix("Received fragments: "))
            for r in updates
            if r["message"].startswith("Received fragments: ")
        ]
        if len(counts) != len(updates) or any(b != a + 1 for a, b in pairwise(counts)):
            raise ValueError("missing/duplicate received-fragment update")
        for slot in range(bins):
            count = sum((r["after_ns"] - start_ns) // 1_000_000_000 == slot for r in updates)
            if count < 2:
                raise ValueError("live committed effective rate below 2 Hz")
    return {
        "updates": len(rows),
        "runs": 10,
        "seconds": bins,
        "rate_rule": "each-1s-bin-at-least-2-v1",
    }
