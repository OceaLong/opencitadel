"""Finite fixture-only preflight for a future failed diagnostic reader/copy.

This checks metadata and reserves logical quotas. It neither authenticates an
original round nor reads a complete ledger, copies bytes, or grants authority.
The eventual reader must re-open and revalidate every identity and byte.
"""

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.native_failed_close import (
    NativeEvidenceManifestV3,
    NativeFailedHostCommitment,
)
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.proof_copy import _identity, _regular, directory

PLAN_BYTES = 64 * 1024
JOURNAL_BYTES = 128 * 1024
JOURNAL_ROWS = 1024
MANIFEST_BYTES = 256 * 1024
COMMAND_BYTES = 96 * 1024
FAILURE_BYTES = 512 * 1024
DRAIN_BYTES = 1024 * 1024
SHARD_BYTES = 512 * 1024
SHARDS = 8
SOURCE_BYTES = 8 * 1024 * 1024
TARGET_BYTES = 8 * 1024 * 1024
COMBINED_BYTES = 16 * 1024 * 1024
WORK_BYTES = 192 * 1024 * 1024
TARGET_HEADROOM_BYTES = 64 * 1024


@dataclass(frozen=True)
class RunnerFailedOriginMap:
    """Caller-owned origin→retained claims; this type alone does not prove them."""

    parent_origin: Path
    parent_retained: Path
    child_origin: Path
    child_retained: Path


@dataclass(frozen=True)
class FailedDiagnosticPreflight:
    """A non-authorizing finite-size observation and logical reservation."""

    state: Literal["preflight-only"]
    origins: RunnerFailedOriginMap
    command_sha256: str
    close_row_digest: str
    members: tuple[tuple[str, int], ...]
    parent_journal_rows: int
    child_journal_rows: int
    source_bytes: int
    target_reserved_bytes: int
    work_reserved_bytes: int


class FixtureDiagnosticBudget:
    """Monotone logical source/work and separate target-disk fixture quotas."""

    def __init__(
        self,
        *,
        source_limit: int = SOURCE_BYTES,
        target_limit: int = TARGET_BYTES,
        combined_limit: int = COMBINED_BYTES,
        work_limit: int = WORK_BYTES,
    ):
        limits = (
            (source_limit, SOURCE_BYTES),
            (target_limit, TARGET_BYTES),
            (combined_limit, COMBINED_BYTES),
            (work_limit, WORK_BYTES),
        )
        if any(type(value) is not int or not 0 < value <= ceiling for value, ceiling in limits):
            raise ValueError("fixed finite diagnostic fixture quota required")
        self.source_limit = source_limit
        self.target_limit = target_limit
        self.combined_limit = combined_limit
        self.work_limit = work_limit
        self.source_reserved = self.target_reserved = self.work_reserved = 0
        self._target_device = None
        self._lock = threading.RLock()

    def reserve(self, source: int, target: int, work: int, *, target_fd: int):
        if any(type(value) is not int or value < 0 for value in (source, target, work)):
            raise ValueError("finite diagnostic reservation required")
        with self._lock:
            next_source = self.source_reserved + source
            next_target = self.target_reserved + target
            next_work = self.work_reserved + work
            if (
                next_source > self.source_limit
                or next_target > self.target_limit
                or next_source + next_target > self.combined_limit
                or next_work > self.work_limit
            ):
                raise ValueError("finite diagnostic source/target/work quota exceeded")
            target_device = os.fstat(target_fd).st_dev
            if self._target_device is not None and target_device != self._target_device:
                raise ValueError("finite diagnostic budget cannot span target filesystems")
            target_space = os.fstatvfs(target_fd)
            available = target_space.f_bavail * target_space.f_frsize
            if available < next_target + TARGET_HEADROOM_BYTES:
                raise ValueError("failed diagnostic cumulative target disk space unavailable")
            self.source_reserved = next_source
            self.target_reserved = next_target
            self.work_reserved = next_work
            self._target_device = target_device

    def recheck_target_space(self, target_fd: int):
        """Recheck the reserved filesystem without another logical debit."""
        with self._lock:
            if self._target_device != os.fstat(target_fd).st_dev:
                raise ValueError("failed diagnostic reserved target filesystem changed")
            space = os.fstatvfs(target_fd)
            if space.f_bavail * space.f_frsize < self.target_reserved + TARGET_HEADROOM_BYTES:
                raise ValueError("failed diagnostic cumulative target disk space unavailable")


def _origin(path: Path):
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute trusted failed origin required")


def _stat_file(root_fd: int, name: str, limit: int, *, nonempty=False):
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError("single private failed member required")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    try:
        before = _regular(fd)
        if before.st_size > limit or (nonempty and before.st_size == 0):
            raise ValueError("failed diagnostic member exceeds fixture bound")
        if _identity(before) != _identity(os.stat(name, dir_fd=root_fd, follow_symlinks=False)):
            raise ValueError("failed diagnostic member changed during stat")
        return before.st_size
    finally:
        os.close(fd)


def _read_small(root_fd: int, name: str, limit: int):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    try:
        before = _regular(fd)
        if not 0 < before.st_size <= limit:
            raise ValueError("failed diagnostic small member exceeds fixture bound")
        raw = bytearray()
        while len(raw) < before.st_size:
            chunk = os.read(fd, before.st_size - len(raw))
            if not chunk:
                raise ValueError("failed diagnostic small member truncated")
            raw.extend(chunk)
        if (
            os.read(fd, 1)
            or _identity(before) != _identity(_regular(fd))
            or _identity(before) != _identity(os.stat(name, dir_fd=root_fd, follow_symlinks=False))
        ):
            raise ValueError("failed diagnostic small member changed during read")
        return bytes(raw)
    finally:
        os.close(fd)


def _journal_rows(root_fd: int, expected_size: int):
    """Count bounded frames without JSON parsing or ledger materialization."""
    fd = os.open("attempt.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    try:
        before = _regular(fd)
        if before.st_size != expected_size or before.st_size > JOURNAL_BYTES:
            raise ValueError("failed diagnostic journal changed after stat")
        read_bytes = rows = line_bytes = 0
        last = b"\n"
        while read_bytes < expected_size:
            chunk = os.read(fd, min(65536, expected_size - read_bytes))
            if not chunk:
                raise ValueError("failed diagnostic journal truncated")
            read_bytes += len(chunk)
            for part in chunk.split(b"\n")[:-1]:
                line_bytes += len(part) + 1
                if line_bytes > JOURNAL_BYTES:
                    raise ValueError("failed diagnostic journal frame exceeds fixture bound")
                rows += 1
                line_bytes = 0
            line_bytes += len(chunk.split(b"\n")[-1])
            if rows > JOURNAL_ROWS:
                raise ValueError("failed diagnostic journal row count exceeds fixture bound")
            last = chunk[-1:]
        if (
            (expected_size and last != b"\n")
            or os.read(fd, 1)
            or _identity(before) != _identity(_regular(fd))
            or _identity(before)
            != _identity(os.stat("attempt.jsonl", dir_fd=root_fd, follow_symlinks=False))
        ):
            raise ValueError("failed diagnostic journal changed or incomplete")
        return rows
    finally:
        os.close(fd)


def preflight_failed_diagnostic(
    trusted_origins: RunnerFailedOriginMap,
    command_sha256: str,
    *,
    fresh_host: NativeFailedHostCommitment,
    target_parent: Path,
    budget: FixtureDiagnosticBudget,
) -> FailedDiagnosticPreflight:
    """Check a small source fixture before any full ledger parse or copy.

    `trusted_origins` and `fresh_host` must come from the runner. This function
    cannot authenticate that provenance or reserve physical disk blocks. Its
    result is never reader/copy authority; the later reader must replay bytes.
    """
    if (
        type(trusted_origins) is not RunnerFailedOriginMap
        or type(fresh_host) is not NativeFailedHostCommitment
        or type(budget) is not FixtureDiagnosticBudget
        or not isinstance(target_parent, Path)
        or command_sha256 != fresh_host.command_sha256
        or fresh_host.state != "failed"
    ):
        raise ValueError("runner-owned failed diagnostic inputs required")
    for path in (trusted_origins.parent_origin, trusted_origins.child_origin):
        _origin(path)
    if trusted_origins.parent_origin == trusted_origins.child_origin:
        raise ValueError("distinct parent/child original origins required")
    if any(
        target_parent == source
        or target_parent.is_relative_to(source)
        or source.is_relative_to(target_parent)
        for source in (trusted_origins.parent_retained, trusted_origins.child_retained)
    ):
        raise ValueError("failed diagnostic source/target path ancestry overlaps")
    with (
        directory(trusted_origins.parent_retained) as parent_fd,
        directory(trusted_origins.child_retained) as child_fd,
        directory(target_parent) as target_fd,
    ):
        identities = {
            (info.st_dev, info.st_ino)
            for info in (os.fstat(parent_fd), os.fstat(child_fd), os.fstat(target_fd))
        }
        if len(identities) != 3:
            raise ValueError("failed diagnostic source/target directory alias")
        members = []
        plan_sizes = []
        journal_sizes = []
        for label, root_fd in (("parent", parent_fd), ("child", child_fd)):
            plan_size = _stat_file(root_fd, "plan.json", PLAN_BYTES, nonempty=True)
            journal_size = _stat_file(root_fd, "attempt.jsonl", JOURNAL_BYTES)
            members.extend(
                ((f"{label}/plan.json", plan_size), (f"{label}/attempt.jsonl", journal_size))
            )
            plan_sizes.append(plan_size)
            journal_sizes.append(journal_size)
        child_plan = _read_small(child_fd, "plan.json", PLAN_BYTES)
        if hashlib.sha256(child_plan).hexdigest() != fresh_host.plan_sha256:
            raise ValueError("failed diagnostic child plan/host receipt differs")
        native_name = f"native-{command_sha256}"
        if not (native_name.isascii() and len(native_name) == 71):
            raise ValueError("failed diagnostic command key differs")
        native_path = trusted_origins.child_retained / native_name
        with directory(native_path) as native_fd:
            if set(os.listdir(native_fd)) != {
                "command.json",
                "manifest.json",
                "failure.json",
                "failure-drain.ndjson",
                "records",
            }:
                raise ValueError("failed diagnostic exact native fileset differs")
            manifest_size = _stat_file(native_fd, "manifest.json", MANIFEST_BYTES, nonempty=True)
            manifest_raw = _read_small(native_fd, "manifest.json", MANIFEST_BYTES)
            if hashlib.sha256(manifest_raw).hexdigest() != fresh_host.manifest_sha256:
                raise ValueError("failed diagnostic manifest/host receipt differs")
            if _canonical(strict_json(manifest_raw)) != manifest_raw:
                raise ValueError("failed diagnostic manifest noncanonical")
            manifest = NativeEvidenceManifestV3.model_validate(strict_json(manifest_raw))
            if (
                manifest.command.sha256 != command_sha256
                or manifest.failure.sha256 != fresh_host.failure_sha256
                or manifest.evidence_state != fresh_host.evidence_state
                or len(manifest.shards) > SHARDS
            ):
                raise ValueError("failed diagnostic manifest exceeds fixture inventory")
            limits = (
                ("command.json", COMMAND_BYTES, manifest.command.size_bytes),
                ("manifest.json", MANIFEST_BYTES, manifest_size),
                ("failure.json", FAILURE_BYTES, manifest.failure.size_bytes),
                ("failure-drain.ndjson", DRAIN_BYTES, manifest.failure_drain.size_bytes),
            )
            for name, limit, claimed in limits:
                actual = _stat_file(native_fd, name, limit, nonempty=name != "failure-drain.ndjson")
                if actual != claimed:
                    raise ValueError("failed diagnostic native member size differs")
                members.append((f"child/{native_name}/{name}", actual))
            with directory(native_path / "records") as records_fd:
                shard_names = {row.path.split("/", 1)[1] for row in manifest.shards}
                if set(os.listdir(records_fd)) != shard_names:
                    raise ValueError("failed diagnostic exact record fileset differs")
                for row in manifest.shards:
                    name = row.path.split("/", 1)[1]
                    actual = _stat_file(records_fd, name, SHARD_BYTES, nonempty=True)
                    if actual != row.size_bytes:
                        raise ValueError("failed diagnostic shard size differs")
                    members.append((f"child/{native_name}/{row.path}", actual))
        source_bytes = sum(size for _, size in members)
        target_bytes = source_bytes
        # Source + copied-target ledgers both charge 64x parsed frames.
        # Twelve source-size passes cover both physical replays, stage probe,
        # copy streams, final source rehash, and bounded per-file windows.
        work_bytes = (
            2 * (sum(plan_sizes) + sum(journal_sizes)) * 64 + source_bytes * 12 + 8 * 1024 * 1024
        )
        if (
            source_bytes > SOURCE_BYTES
            or target_bytes > TARGET_BYTES
            or source_bytes + target_bytes > COMBINED_BYTES
            or work_bytes > WORK_BYTES
        ):
            raise ValueError("failed diagnostic aggregate fixture bound exceeded")
        parent_rows = _journal_rows(parent_fd, journal_sizes[0])
        child_rows = _journal_rows(child_fd, journal_sizes[1])
        budget.reserve(source_bytes, target_bytes, work_bytes, target_fd=target_fd)
        return FailedDiagnosticPreflight(
            state="preflight-only",
            origins=trusted_origins,
            command_sha256=command_sha256,
            close_row_digest=fresh_host.close_row_digest,
            members=tuple(members),
            parent_journal_rows=parent_rows,
            child_journal_rows=child_rows,
            source_bytes=source_bytes,
            target_reserved_bytes=target_bytes,
            work_reserved_bytes=work_bytes,
        )
