"""Bounded strict safe-artifact IO. No resources are started by these helpers."""

import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

from scripts.acceptance.capacity_models import ROLES, Artifact, Fixture

MAX_REPORT_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
# Bounded disk package cap, NOT a resident-memory guarantee. SQL continuity
# snapshots add source evidence beyond progress/paint pairs. The present reader
# still merges all roles; runtime use needs an independently bounded reader.
# Up to 1,200 preregistered windows, 30 s, 10 Runs * 2 Hz = 720,000
# progress+paint pairs; bounded shards retain all of them without giant JSON parses.
MAX_PACKAGE_BYTES = 32 * 1024**3
MAX_ARTIFACTS = 2048
SHARDED = {
    "c2c": {"units"},
    "measurements": {
        "native_bindings",
        "markers",
        "live_paints",
        "samples",
        "sources",
        "browsers",
        "resources",
        "errors",
    },
    "workload": {"windows", "progress", "source_acks", "errors"},
    "cleanup": {"rounds", "cohorts", "dispositions"},
    "network": {"context_ids", "calibrations", "phase_intervals"},
    "resets": {"resets"},
    "diagnostics": {"queries"},
}


def strict_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON number")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("invalid bounded JSON") from error


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _directory(path: Path):
    """Open each absolute directory component relative to its held parent FD."""
    path = path.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in (".", ".."):
                raise ValueError("noncanonical directory")
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _parent(root: Path, relative: str, *, create=False):
    # Lexical validation only; safety comes from anchored nofollow traversal.
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or str(parsed) != relative
        or any(x in ("", ".", "..") for x in relative.split("/"))
        or "\\" in relative
    ):
        raise ValueError("unsafe artifact path")
    fd = _directory(root)
    try:
        for component in parsed.parts[:-1]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, 0o700, dir_fd=fd)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parsed.name
    except BaseException:
        os.close(fd)
        raise


def read_relative(root: Path, relative: str, limit: int) -> bytes:
    parent, name = _parent(root, relative)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("artifact is not a bounded regular file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError("artifact changed while reading or exceeds size limit")
        return data


def read_bounded(path: Path, limit: int) -> bytes:
    return read_relative(path.parent, path.name, limit)


def write_relative(root: Path, relative: str, data: bytes):
    """Atomic replacement at held parent; never truncate an external hardlink."""
    from uuid import uuid4

    parent, name = _parent(root, relative, create=True)
    temporary = ".capacity-" + uuid4().hex
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)


def safe_path(root: Path, relative: str) -> Path:
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or str(parsed) != relative
        or any(x in ("", ".", "..") for x in relative.split("/"))
        or "\\" in relative
        or relative == "report.json"
    ):
        raise ValueError("unsafe/reserved artifact path")
    current = root
    for part in parsed.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink artifact path")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact escapes report directory")
    return current


def _same_parent(root, relative, held):
    current, _ = _parent(root, relative)
    try:
        expected, actual = os.fstat(held), os.fstat(current)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("artifact parent changed during copy")
    finally:
        os.close(current)


def _copy_observation(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def copy_artifacts(artifacts, source: Path, destination: Path):
    """Fresh owned destination, exact copied-byte hashes, one 1MiB IO buffer.

    This is byte transport only: the facade validates the source before calling
    and independently validates every retained role and private original after.
    Previously completed files remain evidence when any later copy fails.
    """
    from uuid import uuid4

    parent = _directory(destination.parent)
    try:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    paths, total = set(), 0
    for item in artifacts:
        if type(item) is not Artifact or item.path in paths or len(paths) >= MAX_ARTIFACTS:
            raise ValueError("invalid artifact copy descriptor")
        paths.add(item.path)
        total += item.size_bytes
        if item.size_bytes > MAX_ARTIFACT_BYTES or total > MAX_PACKAGE_BYTES:
            raise ValueError("artifact copy quota exceeded")
        safe_path(source, item.path)
        source_parent, name = _parent(source, item.path)
        target_parent = source_fd = target_fd = None
        temporary = ".capacity-" + uuid4().hex
        temporary_identity = None
        try:
            source_fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent
            )
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("artifact copy requires regular file")
            if before.st_size != item.size_bytes:
                raise ValueError("artifact copy size differs")
            target_parent, target_name = _parent(destination, item.path, create=True)
            target_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target_parent,
            )
            temporary_identity = os.fstat(target_fd)
            hashed, length = hashlib.sha256(), 0
            while True:
                chunk = os.read(source_fd, min(1024 * 1024, item.size_bytes - length + 1))
                if not chunk:
                    break
                length += len(chunk)
                if length > item.size_bytes:
                    raise ValueError("artifact copy exceeds declared bytes")
                hashed.update(chunk)
                pending = memoryview(chunk)
                while pending:
                    written = os.write(target_fd, pending)
                    if written <= 0:
                        raise OSError("artifact copy write failed")
                    pending = pending[written:]
                del pending, chunk
            if length != item.size_bytes or hashed.hexdigest() != item.sha256:
                raise ValueError("artifact copy digest mismatch")
            after = os.fstat(source_fd)
            named = os.stat(name, dir_fd=source_parent, follow_symlinks=False)
            if _copy_observation(before) != _copy_observation(after) or _copy_observation(
                after
            ) != _copy_observation(named):
                raise ValueError("artifact changed during copy")
            _same_parent(source, item.path, source_parent)
            _same_parent(destination, item.path, target_parent)
            os.fsync(target_fd)
            named = os.stat(temporary, dir_fd=target_parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (
                temporary_identity.st_dev,
                temporary_identity.st_ino,
            ):
                raise ValueError("artifact temporary identity changed")
            os.replace(temporary, target_name, src_dir_fd=target_parent, dst_dir_fd=target_parent)
            named = os.stat(target_name, dir_fd=target_parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (
                temporary_identity.st_dev,
                temporary_identity.st_ino,
            ):
                raise ValueError("retained artifact identity changed")
            os.fsync(target_parent)
            _same_parent(destination, item.path, target_parent)
        finally:
            if target_parent is not None and temporary_identity is not None:
                with contextlib.suppress(FileNotFoundError):
                    actual = os.stat(temporary, dir_fd=target_parent, follow_symlinks=False)
                    if (actual.st_dev, actual.st_ino) == (
                        temporary_identity.st_dev,
                        temporary_identity.st_ino,
                    ):
                        os.unlink(temporary, dir_fd=target_parent)
            for descriptor in (source_fd, target_fd, source_parent, target_parent):
                if descriptor is not None:
                    os.close(descriptor)


def load_artifacts(artifacts: list[Artifact], root: Path):
    if not len(ROLES) + 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ValueError("bounded artifact count requires every safe role")
    paths, roles, retained, shards = set(), {}, {}, {}
    total = 0
    for item in artifacts:
        if item.path in paths:
            raise ValueError("duplicate artifact path")
        paths.add(item.path)
        if item.schema_version != (1 if item.role == "fixture" else 3):
            raise ValueError("unsupported artifact role version")
        if item.size_bytes > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds size limit")
        total += item.size_bytes
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("package exceeds size limit")
        if item.role not in SHARDED and (item.shard_count != 1 or item.shard_index != 0):
            raise ValueError("role is not shardable")
        count, seen = shards.setdefault(item.role, (item.shard_count, set()))
        if count != item.shard_count or item.shard_index in seen or item.shard_index >= count:
            raise ValueError("duplicate/inconsistent artifact shard")
        seen.add(item.shard_index)
        safe_path(root, item.path)
        data = read_relative(root, item.path, item.size_bytes)
        if len(data) != item.size_bytes or hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError("capacity artifact digest mismatch")
        model = Fixture if item.role == "fixture" else ROLES[item.role]
        document = strict_json(data)
        if (
            not isinstance(document, dict)
            or "schema_version" not in document
            or (item.role != "fixture" and document.get("role") != item.role)
        ):
            raise ValueError("artifact content role/schema missing or mismatched")
        parsed = model.model_validate(document)
        if item.role in roles:
            if item.role not in SHARDED:
                raise ValueError("duplicate artifact role")
            original = roles[item.role]
            for field in model.model_fields:
                value = getattr(parsed, field)
                if field in SHARDED[item.role]:
                    getattr(original, field).extend(value)
                elif value != getattr(original, field):
                    raise ValueError("shard role identity mismatch")
        else:
            roles[item.role] = parsed
        retained[item.path] = data
    if set(roles) != {*ROLES, "fixture"} or any(
        len(seen) != count for count, seen in shards.values()
    ):
        raise ValueError("missing capacity artifact role/shard")
    return roles, retained
