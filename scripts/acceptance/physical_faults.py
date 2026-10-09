"""Private, bounded acceptance fault controls. Never imported by product code."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import itertools
import json
import os
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID

ROOT = Path("/tmp/acceptance-physical-fault")
MAX_BYTES = 262144
SOURCE_FILES = (
    "dispatch_audit.py",
    "observed_kernel.py",
    "physical_faults.py",
    "physical_fault_runtime.py",
    "physical_fault_authority.py",
    "physical_fault_command.py",
    "physical_fault_host.py",
    "fault_lifecycle.py",
)


class FaultError(RuntimeError):
    pass


class FaultBusy(FaultError):
    pass


def source_digest():
    return hashlib.sha256(
        b"".join((Path(__file__).parent / name).read_bytes() for name in SOURCE_FILES)
    ).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_current = ContextVar("acceptance_physical_fault", default=None)


@contextmanager
def selected_context(value):
    token = _current.set((asyncio.current_task(), value))
    try:
        yield
    finally:
        _current.reset(token)


def actual_context():
    current = _current.get()
    if current is None:
        return None
    if current[0] is not asyncio.current_task():
        raise FaultError("fault context inherited by a different task")
    return current[1]


class FaultControl:
    """One arm across processes; flock and fsync preserve one-shot evidence."""

    def __init__(self, root=ROOT, *, now=time.monotonic_ns):
        self.root, self.now = Path(root), now
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise FaultError("unsafe fault directory")

    def _open(self, name, flags):
        try:
            fd = os.open(self.root / name, flags | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise FaultError("unsafe fault file") from error
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > MAX_BYTES
        ):
            os.close(fd)
            raise FaultError("unsafe fault file")
        return fd

    @contextmanager
    def lock(self):
        fd = self._open("lock", os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self, name):
        if not (self.root / name).exists() and not (self.root / name).is_symlink():
            return None
        fd = self._open(name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            return json.loads(stream.read(MAX_BYTES + 1))

    def write(self, name, value):
        raw = json.dumps(value, sort_keys=True).encode()
        if len(raw) > MAX_BYTES:
            raise FaultError("fault evidence overflow")
        fd = self._open("next", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(self.root / "next", self.root / name)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            (self.root / "next").unlink(missing_ok=True)

    def arm(self, value):
        value.setdefault("schema_version", 1)
        if value["schema_version"] != 1:
            raise FaultError("unsupported fault schema")
        for key in ("fault_id", "boot_id", "execution_run_id", "activity_id"):
            UUID(value[key])
        if (
            value["kind"] not in {"recorded_object_missing", "shell_receipt_loss"}
            or not self.now() < value["expires_ns"] <= self.now() + 300_000_000_000
        ):
            raise FaultError("invalid fault lifetime/kind")
        with self.lock():
            if self.read("arm.json") is not None:
                raise FaultBusy("active fault already present")
            self.write(
                "journal.json", [{"event": "arm", "fault_id": value["fault_id"], "at": self.now()}]
            )
            self.write("arm.json", value)

    def ordinary_busy(self, expected):
        """Locked classification only; never await authority while holding flock."""
        with self.lock():
            value = self.read("arm.json")
            if value is None:
                return False
            keys = (
                "runner_project",
                "runner_run",
                "invocation_id",
                "binding_sha256",
                "kernel_container",
                "source_sha256",
                "boot_id",
                "owner_user_id",
            )
            if (
                value.get("schema_version") != 1
                or any(not expected.get(key) or value.get(key) != expected[key] for key in keys)
                or type(value.get("expires_ns")) is not int
                or self.now() >= value["expires_ns"]
            ):
                raise FaultError("foreign or expired active control retained")
            try:
                for key in ("fault_id", "execution_run_id", "activity_id"):
                    UUID(value[key])
            except (ValueError, KeyError, TypeError) as error:
                raise FaultError("invalid active fault identity retained") from error
            if (
                value.get("kind") not in {"recorded_object_missing", "shell_receipt_loss"}
                or type(value.get("generation")) is not int
                or value["generation"] < 0
            ):
                raise FaultError("invalid active fault control retained")
            rows = self.rows(value)
            if (
                len(rows) != 1
                or rows[0].get("event") != "arm"
                or type(rows[0].get("at")) is not int
                or not 0 <= rows[0]["at"] <= self.now() < value["expires_ns"]
            ):
                raise FaultError("inflight triggered or poisoned control retained")
            return True

    def selected(self, run, activity, boot):
        with self.lock():
            value = self.read("arm.json")
            if value is None or (value["execution_run_id"], value["activity_id"]) != (
                str(run),
                str(activity),
            ):
                return None
            if value.get("schema_version") != 1:
                raise FaultError("unsupported fault schema")
            if value["boot_id"] != boot:
                raise FaultError("fault boot changed")
            if self.now() >= value["expires_ns"]:
                raise FaultError("fault expired")
            return value

    def rows(self, value):
        rows = self.read("journal.json")
        if not rows or any(row["fault_id"] != value["fault_id"] for row in rows):
            raise FaultError("foreign fault journal")
        return rows

    def event(self, value, event, metadata):
        with self.lock():
            current = self.read("arm.json")
            if current != value:
                raise FaultError("fault changed")
            rows = self.rows(value)
            if event in {"enter", "trigger", "physical_send", "receipt", "mismatch"} and any(
                row["event"] == event for row in rows
            ):
                # Persist failure so caller cannot swallow it and claim success.
                rows.append({"event": "duplicate", "fault_id": value["fault_id"], "at": self.now()})
                self.write("journal.json", rows)
                raise FaultError("duplicate fault invocation")
            rows.append(
                {"event": event, "fault_id": value["fault_id"], "at": self.now(), **metadata}
            )
            self.write("journal.json", rows)

    def disarm(self, fault_id, *, require_complete):
        with self.lock():
            value = self.read("arm.json")
            if value is None or value["fault_id"] != fault_id:
                raise FaultError("disarm identity mismatch")
            rows = self.rows(value)
            events = [row["event"] for row in rows]
            if events.count("enter") != events.count("exit"):
                raise FaultError("fault inflight")
            complete = (
                events.count("trigger") == 1
                and "duplicate" not in events
                and self.now() < value["expires_ns"]
            )
            if require_complete and not complete:
                raise FaultError("fault incomplete")
            rows.append(
                {"event": "disarm", "fault_id": fault_id, "at": self.now(), "complete": complete}
            )
            self.write("journal.json", rows)
            (self.root / "arm.json").unlink()
            return rows


def validate_fault_rows(value, rows):
    expected = (
        ["arm", "enter", "trigger", "mismatch", "exit"]
        if value["kind"] == "recorded_object_missing"
        else ["arm", "enter", "physical_send", "receipt", "trigger", "exit"]
    )
    if (
        [row.get("event") for row in rows] != expected
        or any(row.get("fault_id") != value["fault_id"] for row in rows)
        or any(type(row.get("at")) is not int for row in rows)
        or any(left["at"] > right["at"] for left, right in itertools.pairwise(rows))
    ):
        raise FaultError("fault journal incomplete, reordered or foreign")


def public_binding(value):
    keys = (
        "schema_version",
        "runner_project",
        "runner_run",
        "invocation_id",
        "binding_sha256",
        "kernel_container",
        "kind",
        "execution_run_id",
        "activity_id",
        "generation",
        "approval_id",
        "model_activity_id",
        "model_decision_digest",
        "call_digest",
        "catalog_fingerprint",
        "version_id",
        "revision",
        "slot_id",
        "object_id",
        "object_digest",
        "object_bytes",
        "positive_read",
        "marker_absent_before",
        "sandbox_id",
        "source_entity_id",
    )
    return {key: value[key] for key in keys if key in value}
