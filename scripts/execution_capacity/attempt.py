"""Private host cross-boot, append-only single-use attempt authority.

This records intent, never certifies physical completion. Unacknowledged effects
remain consumed and require exact reconciliation; reopening cannot retry them.
"""

import fcntl
import json
import os
import threading
import time
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from scripts.execution_capacity.ownership import _open_private, _private_directory


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(encode(value)).hexdigest()


def host_clock():
    """Observe the actual Linux boot and time namespace, never an operator label."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    UUID(boot)
    namespace = Path("/proc/self/ns/time").stat()
    if time.get_clock_info("monotonic").implementation != "clock_gettime(CLOCK_MONOTONIC)":
        raise ValueError("Linux CLOCK_MONOTONIC prerequisite unavailable")
    return {
        "boot_id": boot,
        "clock": "CLOCK_MONOTONIC",
        "namespace_device": namespace.st_dev,
        "namespace_inode": namespace.st_ino,
    }


class AttemptLedger:
    @classmethod
    def create(cls, root: Path, plan: dict):
        if root.absolute() != root.resolve():
            raise ValueError("symlink attempt directory")
        root.mkdir(mode=0o700)
        fd = _open_private(root / "plan.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encode(plan) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return cls.open(root, plan)

    @classmethod
    def open(cls, root: Path, plan: dict):
        _private_directory(root)
        lock = _open_private(root / "attempt.lock", os.O_RDWR | os.O_CREAT)
        fd = None
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with os.fdopen(_open_private(root / "plan.json", os.O_RDONLY), "rb") as handle:
                if handle.read() != encode(plan) + b"\n":
                    raise ValueError("immutable attempt plan differs")
            fd = _open_private(root / "attempt.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND)
            result = cls()
            result.root, result.plan, result.fd, result.lock = (
                root,
                json.loads(encode(plan)),
                fd,
                lock,
            )
            result.by_kind = defaultdict(list)
            result.thread_lock = threading.RLock()
            result.control_lock = threading.RLock()
            result.chain, result.rows, result.poisoned, result.closed = (
                digest(plan),
                [],
                False,
                False,
            )
            with os.fdopen(os.dup(fd), "rb") as handle:
                handle.seek(0)
                for line in handle:
                    if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                        raise ValueError("incomplete/oversize attempt row; retained")
                    row = json.loads(line)
                    observed = row.pop("digest")
                    if (
                        row["previous"] != result.chain
                        or row["sequence"] != len(result.rows) + 1
                        or digest(row) != observed
                    ):
                        raise ValueError("attempt chain differs")
                    if encode({**row, "digest": observed}) + b"\n" != line:
                        raise ValueError("noncanonical attempt row; retained")
                    result.chain = observed
                    result.rows.append(row)
                    result.by_kind[row["kind"]].append(row)
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return result
        except BaseException:
            if fd is not None:
                os.close(fd)
            os.close(lock)
            raise

    def bind_clock(self):
        with self.control_lock:
            actual = host_clock()
            prior = self.records("host-clock")
            if prior and (len(prior) != 1 or prior[0]["body"] != actual):
                raise ValueError(
                    "host clock domain changed; prior attempt retained, continuation forbidden"
                )
            if not prior:
                # Legacy host receipts have no comparable epoch authority.
                if any(
                    r["kind"].startswith(("guest-", "qemu-", "calibration-")) for r in self.rows
                ):
                    raise ValueError("legacy host receipts lack clock domain; retained")
                self.append("host-clock", actual)
            return digest(actual)

    def records(self, kind):
        with self.thread_lock:
            if self.closed:
                raise ValueError("attempt ledger closed")
            return tuple(self.by_kind.get(kind, ()))

    def count(self, kind):
        with self.thread_lock:
            if self.closed:
                raise ValueError("attempt ledger closed")
            return len(self.by_kind.get(kind, ()))

    def append(self, kind, body):
        with self.thread_lock:
            return self._append(kind, body)

    def _append(self, kind, body):
        if self.closed or self.poisoned:
            raise ValueError("attempt journal uncertain; reopen for reconciliation")
        row = {"sequence": len(self.rows) + 1, "previous": self.chain, "kind": kind, "body": body}
        hashed = digest(row)
        raw = encode({**row, "digest": hashed}) + b"\n"
        if len(raw) > 1024 * 1024:
            raise ValueError("attempt row exceeds bound; store private digest-bound shard")
        self.poisoned = True
        if os.write(self.fd, raw) != len(raw):
            raise OSError("short attempt journal write; retained")
        os.fsync(self.fd)
        saved = json.loads(encode(row))
        self.rows.append(saved)
        self.by_kind[kind].append(saved)
        self.chain, self.poisoned = hashed, False
        return hashed

    def reserve(self, sample_id, window_id, *, seal_digest):
        with self.thread_lock:
            return self._reserve(sample_id, window_id, seal_digest=seal_digest)

    def _reserve(self, sample_id, window_id, *, seal_digest):
        pairs = {(r["sample_id"], r["physical_window_id"]) for r in self.plan["samples"]}
        if (sample_id, window_id) not in pairs:
            raise ValueError("sample/window absent from immutable plan")
        if any(
            r["kind"] == "reserved"
            and (r["body"]["sample_id"] == sample_id or r["body"]["window_id"] == window_id)
            for r in self.rows
        ):
            raise ValueError("sample/window already consumed, including failed attempt")
        return self.append(
            "reserved", {"sample_id": sample_id, "window_id": window_id, "seal_digest": seal_digest}
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        with self.thread_lock:
            if self.closed:
                return
            self.closed = True
            try:
                os.close(self.fd)
            finally:
                os.close(self.lock)


class ReadOnlyAttemptLedger:
    """Bounded original ledger view; never acquires a lock or observes a clock.

    ``origin`` is supplied only by the owning runner's retained-location map.
    It remains the original location for round predicates after relocation.
    This view checks bytes/chain, not origin authorization or semantic success.
    """

    @classmethod
    def open(
        cls,
        location: Path,
        *,
        origin: Path,
        budget,
        max_plan_bytes: int | None = None,
        max_journal_bytes: int | None = None,
        max_journal_rows: int | None = None,
    ):
        from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

        _private_directory(location)
        if not origin.is_absolute() or ".." in origin.parts:
            raise ValueError("absolute original ledger origin required")
        result = cls()
        result.root, result.location = origin, location
        result.rows, result.by_kind = [], defaultdict(list)
        with os.fdopen(_open_private(location / "plan.json", os.O_RDONLY), "rb") as stream:
            info = os.fstat(stream.fileno())
            if max_plan_bytes is not None and info.st_size > max_plan_bytes:
                raise ValueError("original plan exceeds explicit reader bound")
            budget.check(info.st_size, largest=info.st_size)
            raw = stream.read(budget.row_limit + 1)
            if len(raw) > budget.row_limit:
                raise EvidenceQuotaError("private plan frame quota exceeded")
            if len(raw) != info.st_size:
                raise ValueError("original plan bytes changed")
            budget.reserve(len(raw) * 64, rows=1, largest=len(raw))
            result.plan = json.loads(raw)
            if raw != encode(result.plan) + b"\n":
                raise ValueError("noncanonical original plan")
            after = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("original plan changed during read")
            result.plan_sha256 = sha256(raw).hexdigest()
        result.chain = digest(result.plan)
        hashed = sha256()
        with os.fdopen(_open_private(location / "attempt.jsonl", os.O_RDONLY), "rb") as stream:
            before = os.fstat(stream.fileno())
            if max_journal_bytes is not None and before.st_size > max_journal_bytes:
                raise ValueError("original journal exceeds explicit reader bound")
            budget.check(before.st_size, rows=0)
            journal_bytes = 0
            while True:
                read_limit = budget.row_limit + 1
                if max_journal_bytes is not None:
                    read_limit = min(read_limit, max_journal_bytes - journal_bytes + 1)
                raw = stream.readline(read_limit)
                if not raw:
                    break
                journal_bytes += len(raw)
                if max_journal_bytes is not None and journal_bytes > max_journal_bytes:
                    raise ValueError("original journal exceeds explicit reader bound")
                if max_journal_rows is not None and len(result.rows) >= max_journal_rows:
                    raise ValueError("original journal exceeds explicit row bound")
                if len(raw) > budget.row_limit:
                    raise EvidenceQuotaError("private ledger frame quota exceeded")
                if not raw.endswith(b"\n"):
                    raise ValueError("incomplete original ledger frame")
                budget.reserve(len(raw) * 64, rows=1, largest=len(raw))
                row = json.loads(raw)
                observed = row.pop("digest")
                if (
                    set(row) != {"sequence", "previous", "kind", "body"}
                    or type(row["sequence"]) is not int
                    or row["sequence"] != len(result.rows) + 1
                    or row["previous"] != result.chain
                    or not isinstance(row["kind"], str)
                    or not row["kind"]
                    or digest(row) != observed
                ):
                    raise ValueError("original attempt chain differs")
                if encode({**row, "digest": observed}) + b"\n" != raw:
                    raise ValueError("noncanonical original attempt row")
                hashed.update(raw)
                result.chain = observed
                result.rows.append(row)
                result.by_kind[row["kind"]].append(row)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("original ledger changed during read")
        result.ledger_sha256 = hashed.hexdigest()
        return result

    def records(self, kind):
        return tuple(self.by_kind.get(kind, ()))

    def count(self, kind):
        return len(self.by_kind.get(kind, ()))
