"""Private per-boot rendezvous, immutable facts and atomic rolling marker.

Only coordination facts live here. Client-ready never attests native paint.
"""

import fcntl
import json
import os
import re
import time
from pathlib import Path
from uuid import UUID

from scripts.execution_capacity.attempt import digest, encode
from scripts.execution_capacity.guest_bridge import process_snapshot
from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.ownership import _open_private


class GuestState:
    def __init__(self, root, identity):
        if set(identity) != {
            "attempt_id",
            "sample_id",
            "window_id",
            "nonce",
            "boot_id",
            "source_digest",
        }:
            raise ValueError("fixed guest identity required")
        for key in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id"):
            UUID(identity[key])
        if re.fullmatch(r"[0-9a-f]{64}", identity["source_digest"]) is None:
            raise ValueError("source digest required")
        self.root, self.identity = root, identity
        self.journal = RecoveryJournal(root)
        self.key = identity["window_id"]

    def close(self):
        self.journal.db.close()

    def initialize(self):
        if self.journal.get("bridge_start", self.key) is not None:
            raise ValueError("guest window already consumed")
        self.journal.intent(
            "bridge_start",
            self.key,
            {
                "identity": self.identity,
                "process": process_snapshot(os.getpid()),
                "guest_ns": time.monotonic_ns(),
            },
        )

    def verify(self):
        value = self.journal.parent("bridge_start", self.key)
        if value["identity"] != self.identity:
            raise ValueError("foreign guest boot/window/nonce/source")

    def publish(self, kind, data):
        self.verify()
        self.journal.intent("bridge_" + kind, self.key, data)

    def read(self, kind):
        self.verify()
        row = self.journal.get("bridge_" + kind, self.key)
        return None if row is None else row["body"]

    def process_observation(self):
        self.verify()
        original = self.journal.parent("bridge_start", self.key)["process"]
        try:
            current = process_snapshot(original["pid"])
        except FileNotFoundError:
            current = None
        return {"original": original, "current": current, "same_process": current == original}

    def establish(self, *, sessions, claims, batch_id, start_ns, end_ns, snapshot_id=None):
        rows = [
            {
                "run_id": r,
                "activity_id": a,
                "generation": g,
                "claim_generation": c,
                "call_identity": i,
            }
            for r, a, g, c, i in sorted(claims)
        ]
        body = {
            "identity": self.identity,
            "sessions": sessions,
            "claims": rows,
            "snapshot_id": snapshot_id,
            "batch_id": batch_id,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "established_ns": time.monotonic_ns(),
        }
        self.publish("cohort", {**body, "cohort_digest": digest(body)})

    def client_ready(self, cohort_digest, native_digest):
        cohort = self.read("cohort")
        if (
            cohort is None
            or cohort["cohort_digest"] != cohort_digest
            or time.monotonic_ns() >= cohort["start_ns"]
        ):
            raise ValueError("client readiness missing, foreign or late")
        if len(native_digest) != 64:
            raise ValueError("native evidence digest required")
        self.publish(
            "client_ready",
            {
                "cohort_digest": cohort_digest,
                "native_digest": native_digest,
                "guest_ns": time.monotonic_ns(),
            },
        )

    def close_measurement(self, required_event_ids):
        prior = self.read("measurement_closed")
        if prior is not None:
            return prior
        measured = self.read("measurement")
        now = time.monotonic_ns()
        if now < measured["start_ns"] + measured["rule"]["end_offset_ns"]:
            return None
        rows = self.journal.db.execute(
            "SELECT body FROM intents WHERE kind='bridge_progress' "
            "AND json_extract(body,'$.progress.window_id')=?",
            (self.key,),
        ).fetchall()
        published = {json.loads(row[0])["progress"]["event_id"] for row in rows}
        if not set(required_event_ids) <= published:
            return None
        self.publish("measurement_closed", {"guest_ns": now, "feed_cursor": len(rows)})
        return self.read("measurement_closed")

    def client_done(self, native_digest):
        running = self.read("running")
        now = time.monotonic_ns()
        if running is None or now < running["start_ns"] or now > running["end_ns"]:
            raise ValueError("client-done outside fixed guest window")
        if len(native_digest) != 64:
            raise ValueError("native completion digest required")
        self.publish("client_done", {"native_digest": native_digest, "guest_ns": now})

    def require_done(self):
        running, done = self.read("running"), self.read("client_done")
        if (
            running is None
            or done is None
            or not running["start_ns"] <= done["guest_ns"] <= running["end_ns"]
        ):
            raise ValueError("missing/late client-done; fixed window unchanged")
        return done

    def require_ready(self):
        cohort, ready = self.read("cohort"), self.read("client_ready")
        marker = self.marker()
        if (
            cohort is None
            or ready is None
            or ready["cohort_digest"] != cohort["cohort_digest"]
            or ready["guest_ns"] >= cohort["start_ns"]
            or marker["installed_ns"] >= cohort["start_ns"]
        ):
            raise ValueError("native-ready/initial marker missed original fixed start")
        return ready

    def stamp(self, marker_id, sequence):
        lock = _open_private(self.root / "marker.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._stamp_locked(marker_id, sequence)
        finally:
            os.close(lock)

    def _stamp_locked(self, marker_id, sequence):
        self.verify()
        UUID(marker_id)
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("positive marker sequence required")
        # Serialize short command writers with an immediate SQLite transaction.
        db = self.journal.db
        db.execute("BEGIN IMMEDIATE")
        try:
            previous = db.execute(
                "SELECT body FROM intents WHERE kind='bridge_marker' AND identity=?", (self.key,)
            ).fetchone()
            if previous is not None:
                previous = json.loads(previous[0])
                if previous["sequence"] + 1 != sequence:
                    raise ValueError("marker sequence stale/gapped; no retry")
            elif sequence != 1:
                raise ValueError("first marker must be sequence one")
            row = {
                **self.identity,
                "marker_id": marker_id,
                "sequence": sequence,
                "installed_ns": time.monotonic_ns(),
            }
            db.execute(
                "INSERT INTO intents(kind,identity,body) VALUES('bridge_marker_history',?,?)",
                (marker_id, encode(row).decode()),
            )
            db.execute(
                "INSERT INTO intents(kind,identity,body) VALUES('bridge_marker',?,?) ON CONFLICT(kind,identity) DO UPDATE SET body=excluded.body",
                (self.key, encode(row).decode()),
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        # Atomic rename gives worker readers whole old/new markers. History is
        # fsynced first; response loss preserves the consumed sequence.
        tmp = self.root / ("marker-" + marker_id)
        fd = _open_private(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encode(row))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.root / "current-marker.json")
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return row

    def marker(self):
        return read_marker(self.root, boot_id=self.identity["boot_id"], window_id=self.key)


def read_marker(root: Path, *, boot_id, window_id=None):
    with os.fdopen(_open_private(root / "current-marker.json", os.O_RDONLY), "rb") as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise ValueError("marker exceeds bound")
    row = json.loads(raw)
    if row["boot_id"] != boot_id or (window_id is not None and row["window_id"] != window_id):
        raise ValueError("foreign marker boot/window")
    return row
