"""Acceptance-only, metadata-only observation of actual tool dispatch boundaries."""

from __future__ import annotations

import functools
import json
import os
import time
from pathlib import Path
from uuid import uuid4

MAX_BYTES = 16 * 1024 * 1024
MISMATCH_REASONS = frozenset({"slot_unmatched", "arguments_mismatch", "recorded_object_missing"})


class AuditLog:
    def __init__(self, path, binding):
        self.path = Path(path)
        self.binding = binding
        self.boot_id = str(uuid4())
        self.sequence = 0
        self.size = 0
        # Exclusive file per boot. Launcher removes only its own fixed prior log
        # before creation; boot_id invalidates every earlier positive control.
        self.stream = self.path.open("xb", buffering=0)
        os.chmod(self.path, 0o600)
        self.write("boot", {})

    def write(self, event, metadata):
        record = {
            **self.binding,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "monotonic_ns": time.monotonic_ns(),
            "event": event,
            **metadata,
        }
        data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if self.size + len(data) > MAX_BYTES:
            raise RuntimeError("dispatch audit capacity exceeded")
        if self.stream.write(data) != len(data):
            raise RuntimeError("dispatch audit short write")
        os.fsync(self.stream.fileno())
        self.sequence += 1
        self.size += len(data)

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]


def observe(original, kind, log):
    @functools.wraps(original)
    async def wrapped(self, payload, context, *args, **kwargs):
        activity = getattr(context, "activity_id", None)
        if activity is None or not getattr(context.run, "run_id", None):
            raise RuntimeError("dispatch observer missing actual activity identity")
        metadata = {
            "call_id": str(uuid4()),
            "kind": kind,
            "execution_run_id": str(context.run.run_id),
            "activity_id": str(activity),
            "generation": context.generation,
            "claim_generation": context.claim_generation,
            "owner_user_id": context.owner_user_id,
            "team_id": context.team_id,
        }
        # Never record payload/arguments/result/exception messages or credentials.
        log.write("begin", metadata)
        try:
            result = await original(self, payload, context, *args, **kwargs)
        except BaseException as original_error:
            try:
                reason = getattr(original_error, "reason", None)
                mismatch = (
                    {"mismatch_reason": reason}
                    if kind == "replay"
                    and type(original_error).__name__ == "ReplayMismatch"
                    and reason in MISMATCH_REASONS
                    else {}
                )
                log.write("end", {**metadata, "outcome": type(original_error).__name__, **mismatch})
            except BaseException as logging_error:  # noqa: BLE001 - retain cancellation plus failed audit
                raise BaseExceptionGroup(
                    "dispatch and audit failed", [original_error, logging_error]
                ) from None
            raise
        log.write("end", {**metadata, "outcome": "returned"})
        return result

    return wrapped


def validate_window(records, binding, *, replay_run, positive_runs, owner_user_id):
    """Only catalog-boundary zero, not zero model/storage/network traffic."""
    if (
        not records
        or records[0].get("event") != "boot"
        or not positive_runs
        or replay_run in positive_runs
    ):
        raise ValueError("audit boot and distinct positive controls required")
    boot = records[0].get("boot_id")
    opened = {}
    seen_calls = set()
    counts = {
        identity: {"handler": 0, "replay": 0, "catalog": 0}
        for identity in [replay_run, *positive_runs]
    }
    replay_outcomes = []
    last_time = -1
    for sequence, row in enumerate(records):
        if (
            row.get("sequence") != sequence
            or row.get("boot_id") != boot
            or any(row.get(key) != value for key, value in binding.items())
        ):
            raise ValueError("audit dropped, restarted or foreign")
        if type(row.get("monotonic_ns")) is not int or row["monotonic_ns"] < last_time:
            raise ValueError("invalid audit ordering")
        last_time = row["monotonic_ns"]
        if sequence == 0:
            continue
        if (
            row.get("event") not in {"begin", "end"}
            or row.get("kind") not in {"handler", "replay", "catalog"}
            or not row.get("activity_id")
            or not row.get("execution_run_id")
            or type(row.get("generation")) is not int
            or type(row.get("claim_generation")) is not int
        ):
            raise ValueError("audit identity incomplete")
        identity = row["execution_run_id"]
        if identity in counts and (
            row.get("owner_user_id") != owner_user_id or row.get("team_id") is not None
        ):
            raise ValueError("audit scope mismatch")
        call = row.get("call_id")
        if row["event"] == "begin":
            if not call or call in seen_calls:
                raise ValueError("duplicate interval")
            seen_calls.add(call)
            if row["kind"] != "handler" and not any(
                entry["kind"] == "handler"
                and all(
                    entry[key] == row[key]
                    for key in ("execution_run_id", "activity_id", "generation", "claim_generation")
                )
                for entry in opened.values()
            ):
                raise ValueError("dispatch interval has no matching active handler")
            opened[call] = row
            if identity in counts:
                counts[identity][row["kind"]] += 1
        else:
            before = opened.pop(call, None)
            if (
                before is None
                or any(
                    before.get(key) != row.get(key)
                    for key in (
                        "kind",
                        "execution_run_id",
                        "activity_id",
                        "generation",
                        "claim_generation",
                        "owner_user_id",
                        "team_id",
                    )
                )
                or not row.get("outcome")
            ):
                raise ValueError("unmatched audit end")
            if "mismatch_reason" in row and (
                row["kind"] != "replay"
                or row["outcome"] != "ReplayMismatch"
                or row["mismatch_reason"] not in MISMATCH_REASONS
            ):
                raise ValueError("invalid replay mismatch observation")
            if identity == replay_run and row["kind"] == "replay":
                replay_outcomes.append(
                    {
                        "activity_id": row["activity_id"],
                        "outcome": row["outcome"],
                        "mismatch_reason": row.get("mismatch_reason"),
                    }
                )
    # Unrelated fully-parallel acceptance runs may still be active. Every
    # selected run interval must close; all global identities/sequences are checked.
    if any(row["execution_run_id"] in counts for row in opened.values()):
        raise ValueError("unclosed audit interval")
    if any(
        counts[identity]["catalog"] < 1 or counts[identity]["handler"] < 1
        for identity in positive_runs
    ):
        raise ValueError("source/isolated positive control absent")
    replay = counts[replay_run]
    if replay["replay"] < 1 or replay["handler"] < 1 or replay["catalog"] != 0:
        raise ValueError("replay lacked observation or entered real catalog")
    return {
        "boot_id": boot,
        "last_sequence": len(records) - 1,
        "catalog_entries": replay["catalog"],
        "replay_entries": replay["replay"],
        "replay_outcomes": replay_outcomes,
        "positive_controls": counts,
        "boundary": "AgentToolCatalog.invoke entry; not zero network bytes",
    }
