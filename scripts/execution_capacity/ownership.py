"""Private, append-only ownership inventory, not a resource deletion facility.

Callers record intent BEFORE a product call and acknowledge its exact identity
AFTER readback. A crash between those steps remains pending. Evidence digests
bind separately verified lifecycle readbacks; this ledger cannot verify remote
truth and never treats an intent, missing object, or prefix as ownership proof.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from scripts.acceptance.capacity import FIXTURE_COUNTS

_KINDS = frozenset(
    {
        "run",
        "batch",
        "dataset",
        "configuration",
        "rubric",
        "suite",
        "recording",
        "environment",
        "comparison",
        "export",
        "file",
        "session",
        "team",
        "actor",
        "model",
        "endpoint",
    }
)
_IMMUTABLE = frozenset(
    {
        "run",
        "batch",
        "dataset",
        "configuration",
        "rubric",
        "suite",
        "recording",
        "comparison",
        "export",
    }
)
_FINISHED = frozenset({"retained", "deleted"})


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", value):
        raise ValueError("invalid exact identity")
    return value


def _encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


@dataclass(frozen=True)
class TargetIdentity:
    environment: str
    runtime_id: str
    database_id: str
    scope_id: str

    def __post_init__(self):
        if self.environment != "test":
            raise ValueError("explicit test environment required")
        UUID(self.runtime_id)
        _identifier(self.database_id)
        _identifier(self.scope_id)


def fixture_manifest(
    *, fixture_id: UUID, seed: int, window_end: datetime, target: TargetIdentity
) -> dict:
    if type(seed) is not int or window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("integer seed and timezone-aware window required")
    end = window_end.astimezone(UTC)
    return {
        "schema_version": 1,
        "fixture_id": str(fixture_id),
        "seed": seed,
        "scope_ids": [target.scope_id],
        "counts": dict(FIXTURE_COUNTS),
        "window_start": (end - timedelta(days=90)).isoformat(),
        "window_end": end.isoformat(),
        "status": "planned",
        "target": asdict(target),
        "ownership_journal": "ownership.jsonl",
    }


def _private_directory(path: Path):
    if path.absolute() != path.resolve():
        raise ValueError("symlink ownership directory refused")
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("ownership directory must be private 0700")


def _open_private(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        os.close(fd)
        raise ValueError("ownership file must be private regular 0600")
    return fd


class OwnershipJournal:
    """One exclusive writer; each acknowledged mutation has an fsynced full row.

    Incomplete trailing writes fail closed on resume, retaining original bytes.
    No age-based takeover or automatic truncation is performed.
    """

    def __init__(self, root: Path, manifest: dict, lock_fd: int, journal_fd: int):
        self.root, self.manifest = root, manifest
        self._lock_fd, self._fd = lock_fd, journal_fd
        self._resources: dict[str, dict] = {}
        self._identities: set[tuple[str, str]] = set()
        self._children: dict[str, set[str]] = {}
        self._chain = _digest(manifest)
        self._sequence = 0
        self._poisoned = False

    @property
    def resources(self) -> dict:
        return json.loads(json.dumps(self._resources))

    @classmethod
    def create(cls, root: Path, manifest: dict) -> OwnershipJournal:
        # Exclusivity prevents overwriting another invocation or partial creation.
        cls._validate_manifest(manifest)
        if root.absolute() != root.resolve():
            raise ValueError("symlink ownership directory refused")
        root.mkdir(mode=0o700)
        fd = _open_private(root / "fixture.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(_encoded(manifest) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            # Keep partial inventory for explicit recovery; never remove it.
            raise
        return cls.open(root, TargetIdentity(**manifest["target"]))

    @staticmethod
    def _validate_manifest(manifest):
        expected = fixture_manifest(
            fixture_id=UUID(manifest["fixture_id"]),
            seed=manifest["seed"],
            window_end=datetime.fromisoformat(manifest["window_end"]),
            target=TargetIdentity(**manifest["target"]),
        )
        if manifest != expected:
            raise ValueError("invalid fixture manifest")

    @classmethod
    def open(cls, root: Path, target: TargetIdentity) -> OwnershipJournal:
        _private_directory(root)
        lock_fd = _open_private(root / "ownership.lock", os.O_RDWR | os.O_CREAT)
        journal_fd = None
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("ownership journal busy") from exc
            fd = _open_private(root / "fixture.json", os.O_RDONLY)
            with os.fdopen(fd, "rb") as handle:
                manifest = json.load(handle)
            cls._validate_manifest(manifest)
            if manifest["target"] != asdict(target):
                raise ValueError("foreign target binding")
            journal_fd = _open_private(
                root / "ownership.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND
            )
            result = cls(root, manifest, lock_fd, journal_fd)
            with os.fdopen(os.dup(journal_fd), "rb") as handle:
                handle.seek(0)
                for line in handle:
                    if not line.endswith(b"\n"):
                        raise ValueError("incomplete ownership journal; preserved for recovery")
                    row = json.loads(line)
                    digest = row.pop("digest")
                    if (
                        row.get("previous") != result._chain
                        or row.get("sequence") != result._sequence + 1
                        or digest != _digest(row)
                    ):
                        raise ValueError("ownership journal integrity failure")
                    result._apply(row["action"], row["operation"], row["data"])
                    result._chain, result._sequence = digest, row["sequence"]
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return result
        except BaseException:
            if journal_fd is not None:
                os.close(journal_fd)
            os.close(lock_fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            os.close(self._lock_fd)
            self._fd = None

    def _apply(self, action, operation, data, *, validate_only=False):
        UUID(operation)
        old = self._resources.get(operation)
        if action == "intent":
            if old is not None or data["kind"] not in _KINDS:
                raise ValueError("duplicate operation or unknown resource kind")
            if data["expected_id"] is not None:
                _identifier(data["expected_id"])
            if len(data["parents"]) != len(set(data["parents"])) or any(
                p not in self._resources or self._resources[p]["status"] != "created"
                for p in data["parents"]
            ):
                raise ValueError("unknown or unavailable parent identity")
            updated = {**data, "resource_id": None, "status": "intent"}
        else:
            if old is None:
                raise ValueError("unknown operation; prefix is not ownership")
            updated = {**old}
            if action == "created":
                resource_id = _identifier(data["resource_id"])
                if old["status"] != "intent" or old["expected_id"] not in (None, resource_id):
                    raise ValueError("creation identity or transition mismatch")
                if (old["kind"], resource_id) in self._identities:
                    raise ValueError("resource identity already registered")
                updated.update(status="created", resource_id=resource_id)
            elif action == "cleanup":
                if old["status"] != "created":
                    raise ValueError("invalid cleanup transition")
                if any(
                    self._resources[child]["status"] not in _FINISHED
                    for child in self._children.get(operation, ())
                ):
                    raise ValueError("dependent resource remains unresolved")
                updated["status"] = "cleanup_pending"
            elif action in {"retained", "deleted", "quarantined"}:
                if action == "deleted" and old["kind"] in _IMMUTABLE:
                    raise ValueError("immutable history cannot be deleted")
                if old["status"] != "cleanup_pending":
                    raise ValueError("invalid cleanup acknowledgment transition")
                if not re.fullmatch(r"[0-9a-f]{64}", data["proof_digest"]):
                    raise ValueError("verified proof digest required")
                updated.update(status=action, proof_digest=data["proof_digest"])
            else:
                raise ValueError("unknown journal action")
        if not validate_only:
            self._resources[operation] = updated
            if action == "created":
                self._identities.add((updated["kind"], updated["resource_id"]))
            elif action == "intent":
                for parent in updated["parents"]:
                    self._children.setdefault(parent, set()).add(operation)

    def _record(self, action, operation, data):
        if self._fd is None or self._poisoned:
            raise ValueError("closed or uncertain ownership writer")
        operation = str(operation)
        old = self._resources.get(operation)
        if old is not None:
            if action == "intent" and all(
                old[key] == data[key] for key in ("kind", "expected_id", "parents")
            ):
                return
            if action == "created" and old["resource_id"] == data["resource_id"]:
                return
            if action == "cleanup" and old["status"] in {"cleanup_pending", *_FINISHED}:
                return
            if (
                action in {"retained", "deleted", "quarantined"}
                and old["status"] == action
                and old.get("proof_digest") == data["proof_digest"]
            ):
                return
        self._apply(action, operation, data, validate_only=True)
        row = {
            "sequence": self._sequence + 1,
            "previous": self._chain,
            "action": action,
            "operation": operation,
            "data": data,
        }
        digest = _digest(row)
        encoded = _encoded({**row, "digest": digest}) + b"\n"
        try:
            written = os.write(self._fd, encoded)
            if written != len(encoded):
                raise OSError("short ownership write")
            os.fsync(self._fd)
        except BaseException:
            self._poisoned = True
            raise
        self._apply(action, operation, data)
        self._chain, self._sequence = digest, row["sequence"]

    def intent(self, operation: UUID, kind: str, *, expected_id: str | None = None, parents=()):
        self._record(
            "intent",
            operation,
            {"kind": kind, "expected_id": expected_id, "parents": [str(p) for p in parents]},
        )

    def created(self, operation: UUID, resource_id: str):
        self._record("created", operation, {"resource_id": resource_id})

    def begin_cleanup(self, operation: UUID):
        self._record("cleanup", operation, {})

    def retained(self, operation: UUID, *, proof_digest: str):
        self._record("retained", operation, {"proof_digest": proof_digest})

    def deleted(self, operation: UUID, *, proof_digest: str):
        self._record("deleted", operation, {"proof_digest": proof_digest})

    def quarantine(self, operation: UUID, *, proof_digest: str):
        self._record("quarantined", operation, {"proof_digest": proof_digest})

    def pending(self) -> list[str]:
        if self._poisoned:
            raise ValueError("uncertain ownership writer; reopen and verify durable inventory")
        return [
            op for op, resource in self._resources.items() if resource["status"] not in _FINISHED
        ]
