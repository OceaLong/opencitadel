"""Test-only finite shards from an actual verified private projection stream.

The stream is not a capacity measurement. A failed replay leaves its owned
spool prefix visible and never returns public artifact descriptors.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from api.tests.scripts.capacity_population_writer import SHARD_TARGET_BYTES, FiniteRoleWriter
from scripts.acceptance.capacity_io import MAX_ARTIFACT_BYTES, MAX_PACKAGE_BYTES, strict_json
from scripts.acceptance.capacity_models import Seal

_FIELDS = ("units", "queries", "rounds", "cohorts")
_COUNT_FIELDS = ("runs", "formal_events", "observations", "visible_steps")


class _OwnedSpool:
    def __init__(self, root: Path, *, limit_bytes: int):
        if (
            not root.is_absolute()
            or not root.is_dir()
            or stat.S_IMODE(root.stat().st_mode) != 0o700
            or type(limit_bytes) is not int
            or not 0 < limit_bytes <= MAX_PACKAGE_BYTES
        ):
            raise ValueError("private finite spool root/limit required")
        self.root, self.limit = root, limit_bytes
        self.total = 0
        self.files = {}
        self.hashes = {field: hashlib.sha256() for field in _FIELDS}
        self.counts = dict.fromkeys(_FIELDS, 0)
        for field in _FIELDS:
            path = root / f"{field}.ndjson"
            self.files[field] = os.open(
                path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )

    def append(self, field, value):
        if field not in self.files:
            raise ValueError("unknown private projection family")
        raw = (
            json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode()
            + b"\n"
        )
        if len(raw) > SHARD_TARGET_BYTES // 2 or self.total + len(raw) > self.limit:
            raise ValueError("private projection spool quota exceeded")
        descriptor = self.files[field]
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise OSError("private projection spool write made no progress")
            written += count
        os.fsync(descriptor)
        self.hashes[field].update(raw)
        self.total += len(raw)
        self.counts[field] += 1

    def rows(self, field):
        descriptor = self.files[field]
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError("private projection spool file changed")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as stream:
            stream.seek(0)
            for line in stream:
                if len(line) > SHARD_TARGET_BYTES // 2 or not line.endswith(b"\n"):
                    raise ValueError("private projection spool line differs")
                digest.update(line)
                yield strict_json(line)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or digest.digest() != self.hashes[field].digest():
            raise ValueError("private projection spool changed during replay")

    def close(self):
        for descriptor in self.files.values():
            os.close(descriptor)
        self.files.clear()


def _single(root, role, document):
    if role != "seal":
        raise ValueError("only the finite seal singleton may be emitted")
    Seal.model_validate(document)
    raw = json.dumps(document, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("private seal exceeds singleton artifact limit")
    name = "seal.json"
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise OSError("private seal write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "path": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "role": role,
        "schema_version": 3,
        "shard_index": 0,
        "shard_count": 1,
    }


class OriginalProjectionShards:
    """Single-use sink for `OfflineProofContext.stream_projection`."""

    def __init__(self, root: Path, *, spool_limit_bytes=MAX_PACKAGE_BYTES):
        if (
            not root.is_absolute()
            or not root.is_dir()
            or stat.S_IMODE(root.stat().st_mode) != 0o700
        ):
            raise ValueError("private public-artifact root required")
        spool_root = root / "projection-spool"
        spool_root.mkdir(mode=0o700)
        self.root = root
        self.spool = _OwnedSpool(spool_root, limit_bytes=spool_limit_bytes)
        self.seal = None
        self.version = None
        self.origin_hash = hashlib.sha256()
        self.origins = 0
        self.totals = dict.fromkeys(_COUNT_FIELDS, 0)
        self.finished = False
        self.descriptors = None

    def _open(self):
        if self.finished:
            raise ValueError("private projection sink already finished")

    def _cohort(self, value):
        self.spool.append("cohorts", value)
        for name in _COUNT_FIELDS:
            self.totals[name] += getattr(value.source, name)

    def base(self, seal, unit):
        self._open()
        if self.seal is not None or unit.kind != "base":
            raise ValueError("private projection base identity differs")
        self.seal = seal
        self.version = unit.schema_version
        self.spool.append("units", unit)
        for cohort in seal.cohorts:
            self._cohort(cohort)

    def query(self, value):
        self._open()
        if self.seal is None:
            raise ValueError("private diagnostic before base")
        self.spool.append("queries", value)

    def round(self, value):
        self._open()
        if self.seal is None or value.origin.seal_id != self.seal.seal_id:
            raise ValueError("private round before/mismatched base")
        self.spool.append("rounds", value)
        for cohort in value.cohorts:
            self._cohort(cohort)

    def origin(self, value):
        self._open()
        if self.origins != self.spool.counts["rounds"] - 1:
            raise ValueError("private round/origin order differs")
        self.origin_hash.update(
            json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        )
        self.origin_hash.update(b"\n")
        self.origins += 1

    def unit(self, value):
        self._open()
        if (
            self.seal is None
            or value.kind != "round"
            or value.schema_version != self.version
            or self.spool.counts["units"] != self.origins
        ):
            raise ValueError("private round/unit order differs")
        self.spool.append("units", value)

    def finish(self, count, digest):
        self._open()
        if (
            self.seal is None
            or count != self.origins
            or count != self.spool.counts["rounds"]
            or self.spool.counts["units"] != count + 1
            or digest != self.origin_hash.hexdigest()
        ):
            raise ValueError("private projection terminal closure differs")
        common = {
            "schema_version": 3,
            "attempt_id": self.seal.attempt_id,
            "protocol_id": self.seal.protocol_id,
        }
        descriptions = [_single(self.root, "seal", self.seal.model_dump(mode="json"))]
        configurations = (
            ("c2c", ("units",), {"projection_version": self.version}),
            ("diagnostics", ("queries",), {}),
            (
                "cleanup",
                ("rounds", "cohorts", "dispositions"),
                {
                    "total": self.totals,
                    "pending": [],
                    "quarantined": [],
                    "status": "retained_immutable",
                },
            ),
        )
        for role, fields, extra in configurations:
            writer = FiniteRoleWriter(
                self.root,
                role,
                controls={**common, "role": role, **extra},
                list_fields=fields,
                allow_empty=role == "diagnostics",
            )
            for field in fields:
                writer.start(field)
                for row in () if field == "dispositions" else self.spool.rows(field):
                    writer.append(field, row)
                writer.finish_field(field)
            descriptions.extend(writer.close())
        # One final side-effect boundary: callers may publish only this return.
        self.finished = True
        self.descriptors = tuple(descriptions)
        self.spool.close()
        return self.descriptors

    def close(self):
        if self.spool.files:
            self.spool.close()
