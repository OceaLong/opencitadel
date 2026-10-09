"""Descriptor-safe bounded file bytes used by private source/build manifests."""

import os
import stat
from hashlib import sha256

from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


def stream_file(path, *, budget, consumer):
    if path.absolute() != path.resolve():
        raise ValueError("symlink source file refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("regular source file required")
        budget.reserve(before.st_size, rows=1)
        read = 0
        while read < before.st_size:
            chunk = handle.read(min(64 * 1024, before.st_size - read))
            if not chunk:
                raise ValueError("source file truncated during read")
            read += len(chunk)
            consumer(chunk)
        if handle.read(1):
            raise EvidenceQuotaError("source file grew beyond checked quota")
        after = os.fstat(handle.fileno())
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("source file changed during read")
    return read


def file_digest(path, *, budget=None):
    budget = budget if budget is not None else EvidenceBudget(bytes_limit=32 * 1024 * 1024)
    hashed = sha256()
    stream_file(path, budget=budget, consumer=hashed.update)
    return hashed.hexdigest()
