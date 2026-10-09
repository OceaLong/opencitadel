"""Test-only single-artifact copy-on-write capacity mutation.

The unchanged source package remains authoritative only after the shared
reader freshly checks its bytes in the new session. Rehashed test bytes are
not independent original, native host, or runtime provenance.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from scripts.acceptance.capacity_io import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    read_relative,
    safe_path,
    strict_json,
)


def _private(root):
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or not root.is_dir()
        or root.is_symlink()
        or stat.S_IMODE(root.stat().st_mode) != 0o700
    ):
        raise ValueError("private copy-on-write package root required")


def _write(root, relative, data):
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise OSError("copy-on-write mutation made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def mutate_one_shard(source_root, destination, artifacts, *, role, shard_index, mutate):
    """Link untouched immutable files and replace exactly one selected shard.

    Each invocation owns a fresh private destination. The caller reopens a new
    PackageSession and Report4 derivation/validation; no role model is cached.
    """
    _private(source_root)
    _private(destination)
    if (
        any(destination.iterdir())
        or type(artifacts) not in (list, tuple)
        or not 1 <= len(artifacts) <= MAX_ARTIFACTS
        or type(shard_index) is not int
        or shard_index < 0
    ):
        raise ValueError("fresh bounded mutation destination required")
    matches = [
        i
        for i, item in enumerate(artifacts)
        if item["role"] == role and item.get("shard_index", 0) == shard_index
    ]
    if len(matches) != 1:
        raise ValueError("one exact source shard required")
    target_index = matches[0]
    result = []
    seen = set()
    for ordinal, item in enumerate(artifacts):
        relative = item["path"]
        if relative in seen:
            raise ValueError("duplicate source artifact path")
        seen.add(relative)
        source = safe_path(source_root, relative)
        target = safe_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if ordinal == target_index:
            raw = read_relative(source_root, relative, MAX_ARTIFACT_BYTES)
            if len(raw) != item["size_bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("source mutation shard changed before copy")
            document = strict_json(raw)
            if type(document) is not dict:
                raise ValueError("mutation target must be a role object")
            mutate(document)
            changed = json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            if not changed or len(changed) > MAX_ARTIFACT_BYTES:
                raise ValueError("mutated shard exceeds product bound")
            _write(destination, relative, changed)
            result.append(
                {**item, "size_bytes": len(changed), "sha256": hashlib.sha256(changed).hexdigest()}
            )
        else:
            before = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_size != item["size_bytes"]:
                raise ValueError("source package file differs before link")
            os.link(source, target, follow_symlinks=False)
            after = source.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("source package file changed during link")
            result.append(dict(item))
    return result
