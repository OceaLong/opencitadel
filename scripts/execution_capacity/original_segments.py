"""Owned append-only original byte segments with two bounded data descriptors.

Each instance holds at most one file descriptor. Its journal supplies the held
directory checks and private EXCL opens; paths are derived only from ordinals.
"""

import os
from hashlib import sha256

BUFFER = 64 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class OriginalSegments:
    def __init__(self, owner, stream, *, chunk_bytes, creating, descriptors=None):
        if (
            stream not in ("body", "log", "candidate")
            or type(chunk_bytes) is not int
            or not 4096 <= chunk_bytes <= DEFAULT_CHUNK_BYTES
        ):
            raise ValueError("bounded original segment configuration required")
        self.owner, self.stream, self.chunk_bytes = owner, stream, chunk_bytes
        self.creating, self.fd, self.ordinal = creating, None, None
        self.chunks, self.stamps = [], {}
        self.length = 0
        self._hash = None
        if creating:
            try:
                self._select(0, append=True)
                self.sync()
            except BaseException:
                self.close()
                raise
        else:
            if type(descriptors) is not list or not descriptors:
                raise ValueError("complete original segments required")
            for ordinal, descriptor in enumerate(descriptors):
                if (
                    type(descriptor) is not dict
                    or set(descriptor) != {"ordinal", "bytes", "sha256"}
                    or type(descriptor["ordinal"]) is not int
                    or descriptor["ordinal"] != ordinal
                    or type(descriptor["bytes"]) is not int
                    or not 0 <= descriptor["bytes"] <= chunk_bytes
                    or (ordinal < len(descriptors) - 1 and descriptor["bytes"] != chunk_bytes)
                    or (ordinal > 0 and descriptor["bytes"] == 0)
                    or type(descriptor["sha256"]) is not str
                    or len(descriptor["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in descriptor["sha256"])
                ):
                    raise ValueError("original segment closure differs")
                owner.budget.reserve(512, rows=1)
                self.chunks.append(dict(descriptor))
                self.length += descriptor["bytes"]

    def name(self, ordinal):
        if type(ordinal) is not int or not 0 <= ordinal <= 999999:
            raise ValueError("bounded original segment ordinal required")
        if self.stream == "candidate":
            return f"candidate-{ordinal:06}.bin"
        return (
            f"{ordinal:06}.bin"
            if self.stream == "body"
            else "occurrences.jsonl"
            if ordinal == 0
            else f"{ordinal:06}.jsonl"
        )

    def _check(self):
        if self.fd is None:
            return
        self.owner._check_file(self.name(self.ordinal), self.fd)
        current = _stamp(os.fstat(self.fd))
        if self.ordinal in self.stamps and self.stamps[self.ordinal] != current:
            raise ValueError("original segment identity or bytes changed")
        if current[2] != self.chunks[self.ordinal]["bytes"]:
            raise ValueError("original segment bytes differ")
        self.stamps[self.ordinal] = current

    def _select(self, ordinal, *, append=False):
        if self.ordinal == ordinal and self.fd is not None:
            self._check()
            return
        self.close()
        new = append and ordinal == len(self.chunks)
        if new:
            if not self.creating:
                raise ValueError("readonly original segments")
            self.owner.budget.reserve(512, rows=1)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        else:
            if not 0 <= ordinal < len(self.chunks):
                raise ValueError("original segment range differs")
            flags = os.O_RDWR if self.creating else os.O_RDONLY
        fd = self.owner._file(self.name(ordinal), flags)
        self.fd, self.ordinal = fd, ordinal
        if new:
            self._hash = sha256()
            self.chunks.append({"ordinal": ordinal, "bytes": 0, "sha256": self._hash.hexdigest()})
        self._check()
        if new:
            # A durable begin cannot precede durability of the newly named log
            # segment. Subsequent data sync alone cannot commit its directory.
            os.fsync(fd)
            os.fsync(self.owner.directory)

    def write(self, raw):
        if not self.creating:
            raise ValueError("readonly original segments")
        offset = 0
        while offset < len(raw):
            ordinal = self.length // self.chunk_bytes
            self._select(ordinal, append=True)
            descriptor = self.chunks[ordinal]
            size = min(len(raw) - offset, self.chunk_bytes - descriptor["bytes"])
            part = memoryview(raw)[offset : offset + size]
            os.lseek(self.fd, descriptor["bytes"], os.SEEK_SET)
            written = 0
            while written < size:
                count = os.write(self.fd, part[written:])
                if count <= 0:
                    raise OSError("incomplete original segment write")
                written += count
            self._hash.update(part)
            descriptor["bytes"] += size
            descriptor["sha256"] = self._hash.hexdigest()
            self.length += size
            self.stamps[ordinal] = _stamp(os.fstat(self.fd))
            self._check()
            offset += size
            if descriptor["bytes"] == self.chunk_bytes:
                self.sync()

    def read(self, size, offset):
        if (
            type(size) is not int
            or type(offset) is not int
            or not 0 <= offset <= offset + size <= self.length
        ):
            raise ValueError("original segment read range differs")
        self.owner.budget.reserve(size * 2, rows=0)
        result = bytearray()
        while len(result) < size:
            position = offset + len(result)
            ordinal, local = divmod(position, self.chunk_bytes)
            self._select(ordinal)
            count = min(size - len(result), self.chunks[ordinal]["bytes"] - local)
            chunk = os.pread(self.fd, count, local)
            self._check()
            if len(chunk) != count or not chunk:
                raise ValueError("original segment truncated")
            result.extend(chunk)
        return bytes(result)

    def verify(self, length, digest):
        if self.length != length:
            raise ValueError("original segment total bytes differ")
        aggregate = sha256()
        for descriptor in self.chunks:
            hashed, offset = sha256(), descriptor["ordinal"] * self.chunk_bytes
            self._select(descriptor["ordinal"])
            for relative in range(0, descriptor["bytes"], BUFFER):
                raw = self.read(min(BUFFER, descriptor["bytes"] - relative), offset + relative)
                hashed.update(raw)
                aggregate.update(raw)
            if hashed.hexdigest() != descriptor["sha256"]:
                raise ValueError("original segment bytes differ")
            self._check()
        if aggregate.hexdigest() != digest:
            raise ValueError("original segment aggregate bytes differ")

    def lines(self, limit):
        self.owner.budget.reserve(min(self.length, limit + BUFFER) * 2, rows=0)
        pending = bytearray()
        for offset in range(0, self.length, BUFFER):
            pending.extend(self.read(min(BUFFER, self.length - offset), offset))
            while True:
                end = pending.find(b"\n")
                if end < 0:
                    break
                if end + 1 > limit:
                    raise ValueError("invalid original journal frame")
                yield bytes(pending[: end + 1])
                del pending[: end + 1]
            if len(pending) > limit:
                raise ValueError("invalid original journal frame")
        if pending:
            raise ValueError("incomplete original journal frame")

    def sync(self):
        self._check()
        if self.fd is not None:
            os.fsync(self.fd)
            self._check()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
