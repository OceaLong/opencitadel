"""Bounded raw native wire reader; the host ledger remains its outside authority.

C3b must durably append the exact NativeRecord bytes, acknowledge only after
that append, and commit this manifest digest in the actual host attempt ledger.
This consumer cannot turn a structurally valid prefix into a capacity pass.
"""

import base64
import binascii
import hashlib
import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, StrictStr, model_validator
from scripts.acceptance.capacity_completion import validate_native_trace_completion
from scripts.acceptance.capacity_index import CapacityIndex
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import NativeCommand, NativeFailureSnapshot, NativeRecord
from scripts.acceptance.capacity_records import ID, Digest, Nat, Pos, Record
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_png import validate_native_png
from scripts.execution_capacity.ownership import _private_directory
from scripts.execution_capacity.proof_copy import (
    _identity,
    _regular,
    copy_private,
    directory,
    parent,
)

LINE_BYTES = 96 * 1024
SHARD_BYTES = 32 * 1024 * 1024
MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 65_536
MAX_BINARY_ARTIFACTS = 250
IDENTITY = (
    "attempt_id",
    "protocol_id",
    "sample_id",
    "action_id",
    "context_id",
    "page_id",
    "window_id",
    "clock_id",
)


class NativeFile(Record):
    path: Annotated[StrictStr, Field(min_length=1, max_length=255)]
    sha256: Digest
    size_bytes: Nat

    @model_validator(mode="after")
    def safe_relative_path(self):
        relative = PurePosixPath(self.path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != self.path
            or "\\" in self.path
        ):
            raise ValueError("native raw relative path differs")
        return self


class NativeShard(NativeFile):
    ordinal: Nat
    first_sequence: Pos
    last_sequence: Pos
    records: Pos

    @model_validator(mode="after")
    def bounds(self):
        if (
            self.path != f"records/{self.ordinal:06}.ndjson"
            or not 0 < self.size_bytes <= SHARD_BYTES
        ):
            raise ValueError("native shard path/size differs")
        if self.last_sequence != self.first_sequence + self.records - 1:
            raise ValueError("native shard sequence closure differs")
        return self


class NativeArtifact(Record):
    ordinal: Nat
    purpose: Literal["image", "native-trace", "rejected-capture"]
    artifact_id: ID
    sha256: Digest
    size_bytes: Pos
    chunk_sequences: Annotated[list[Pos], Field(min_length=1, max_length=MAX_RECORDS)]
    owner_sequence: Pos | None

    @model_validator(mode="after")
    def bounds(self):
        if self.purpose == "image" and self.size_bytes > 8 * 1024 * 1024:
            raise ValueError("native retained image exceeds limit")
        if self.purpose == "native-trace" and self.size_bytes > SHARD_BYTES:
            raise ValueError("native trace exceeds limit")
        if self.purpose == "rejected-capture" and self.size_bytes > 128 * 1024 * 1024:
            raise ValueError("native failure artifact exceeds limit")
        if (
            self.purpose != "native-trace"
            and len(self.chunk_sequences) != (self.size_bytes + 49151) // 49152
        ):
            raise ValueError("native artifact chunk closure differs")
        if any(
            a >= b for a, b in zip(self.chunk_sequences, self.chunk_sequences[1:], strict=False)
        ):
            raise ValueError("native artifact chunk record order differs")
        if (self.purpose in {"image", "native-trace"}) != (self.owner_sequence is not None):
            raise ValueError("native artifact terminal owner differs")
        if self.owner_sequence is not None and self.owner_sequence <= self.chunk_sequences[-1]:
            raise ValueError("native artifact terminal precedes chunks")
        if self.purpose == "native-trace" and self.artifact_id != "native-trace":
            raise ValueError("native trace artifact identity differs")
        return self


class NativeEvidenceManifest(Record):
    schema_id: Literal["opencitadel.native-evidence.v2"] = Field(alias="schema")
    state: Literal["complete", "failed"]
    command: NativeFile
    shards: Annotated[list[NativeShard], Field(min_length=1, max_length=MAX_RECORDS)]
    artifacts: Annotated[list[NativeArtifact], Field(max_length=MAX_BINARY_ARTIFACTS)]
    failure: NativeFile | None

    @model_validator(mode="after")
    def closed_inventory(self):
        if self.command.path != "command.json" or self.command.size_bytes > LINE_BYTES:
            raise ValueError("native command descriptor differs")
        if (self.state == "complete") != (self.failure is None):
            raise ValueError("native failure descriptor/state differs")
        if self.failure is not None and self.failure.path != "failure.json":
            raise ValueError("native failure path differs")
        expected = 1
        for ordinal, shard in enumerate(self.shards):
            if shard.ordinal != ordinal or shard.first_sequence != expected:
                raise ValueError("native shard range differs")
            expected = shard.last_sequence + 1
        if expected - 1 > MAX_RECORDS:
            raise ValueError("native record count exceeds wire limit")
        for ordinal, artifact in enumerate(self.artifacts):
            if artifact.ordinal != ordinal:
                raise ValueError("native artifact ordinal differs")
        paths = ["manifest.json", self.command.path]
        paths.extend(row.path for row in self.shards)
        if self.failure is not None:
            paths.append(self.failure.path)
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate native raw file member")
        return self


@dataclass(frozen=True)
class NativeHostCommitment:
    """Exact values copied from a verified host attempt-ledger closeout, not raw files."""

    manifest_sha256: str
    command_sha256: str
    last_ack_sequence: int
    state: Literal["complete", "failed"]

    def __post_init__(self):
        for digest in (self.manifest_sha256, self.command_sha256):
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("verified native host digest required")
        if (
            type(self.last_ack_sequence) is not int
            or not 1 <= self.last_ack_sequence <= MAX_RECORDS
        ):
            raise ValueError("verified native host ACK boundary required")
        if self.state not in ("complete", "failed"):
            raise ValueError("verified native host state required")


def _receipt(root, relative, *, budget, limit):
    hashed = hashlib.sha256()
    with _parent(root, relative) as (directory_fd, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            before = _regular(descriptor)
            if before.st_size > limit:
                raise ValueError("native raw file exceeds fixed limit")
            budget.reserve(before.st_size + 2 * min(before.st_size + 1, 65536), rows=1)
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise ValueError("native raw file truncated")
                hashed.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1) or _identity(before) != _identity(_regular(descriptor)):
                raise ValueError("native raw file changed during read")
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(before) != _identity(named):
                raise ValueError("native raw path changed during read")
            return {"sha256": hashed.hexdigest(), "size_bytes": before.st_size}
        finally:
            os.close(descriptor)


def _read_verified(root, row, *, budget, limit):
    if row.size_bytes > limit:
        raise ValueError("native raw document exceeds fixed limit")
    budget.reserve(row.size_bytes * 4 + 65536, rows=1)
    with _parent(root, row.path) as (directory_fd, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            before = _regular(descriptor)
            if before.st_size != row.size_bytes:
                raise ValueError("native raw document size differs")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(row.size_bytes + 1)
            if (
                len(raw) != row.size_bytes
                or hashlib.sha256(raw).hexdigest() != row.sha256
                or _identity(before) != _identity(_regular(descriptor))
                or _identity(before)
                != _identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
            ):
                raise ValueError("native raw document changed")
            return raw
        finally:
            os.close(descriptor)


@contextmanager
def _parent(root, relative):
    with directory(root) as root_fd, parent(root_fd, relative) as leaf:
        yield leaf


def _check_members(root, manifest):
    expected = {"manifest.json", manifest.command.path}
    expected.update(row.path for row in manifest.shards)
    if manifest.failure is not None:
        expected.add(manifest.failure.path)
    actual = set()
    with directory(root):
        for path in root.iterdir():
            if path.name == "records" and path.is_dir() and not path.is_symlink():
                with directory(path):
                    for child in path.iterdir():
                        if not child.is_file() or child.is_symlink():
                            raise ValueError("native raw foreign file type")
                        actual.add(path.name + "/" + child.name)
            elif path.is_file() and not path.is_symlink():
                actual.add(path.name)
            else:
                raise ValueError("native raw foreign member")
            if len(actual) > len(expected):
                raise ValueError("native raw extra member")
    if actual != expected:
        raise ValueError("native raw missing/foreign member")


def _canonical(value):
    """JSON.stringify-compatible bytes for the strict native JSON value domain.

    JS writes unpaired UTF-16 surrogates as six-byte escapes and emits other
    Unicode literally. JSON numeric fields are finite JS Numbers; host ns are
    decimal strings, so no integer beyond the JS safe range is legal here.
    """

    def quote(value):
        raw = json.dumps(value, ensure_ascii=False)
        return "".join(
            f"\\u{ord(char):04x}" if 0xD800 <= ord(char) <= 0xDFFF else char for char in raw
        ).encode()

    def number(value):
        if type(value) is int:
            if abs(value) > 9007199254740991:
                try:
                    return number(float(value))
                except OverflowError as error:
                    raise ValueError("native JS number exceeds finite range") from error
            return str(value).encode()
        if not math.isfinite(value):
            raise ValueError("native JS nonfinite number")
        if value == 0:
            return b"0"
        decimal = Decimal(repr(value))
        magnitude = abs(value)
        if 1e-6 <= magnitude < 1e21:
            return (
                format(decimal, "f").rstrip("0").rstrip(".").encode()
                if "." in format(decimal, "f")
                else format(decimal, "f").encode()
            )
        scientific = format(decimal.normalize(), "e")
        significand, exponent = scientific.split("e")
        exponent_number = int(exponent)
        return f"{significand}e{'+' if exponent_number >= 0 else ''}{exponent_number}".encode()

    def encode(item):
        if item is None:
            return b"null"
        if item is True:
            return b"true"
        if item is False:
            return b"false"
        if type(item) is str:
            return quote(item)
        if type(item) in (int, float):
            return number(item)
        if type(item) is list:
            return b"[" + b",".join(encode(child) for child in item) + b"]"
        if type(item) is dict and all(type(key) is str for key in item):
            return (
                b"{"
                + b",".join(quote(key) + b":" + encode(child) for key, child in item.items())
                + b"}"
            )
        raise ValueError("native JS JSON value domain differs")

    return encode(value)


class NativeEvidenceSession:
    def __init__(self):
        raise TypeError("open a verified host-bound native evidence session")

    @classmethod
    def open(cls, root, *, host, budget, index_bytes):
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or ".." in root.parts
            or type(host) is not NativeHostCommitment
            or type(budget) is not EvidenceBudget
            or type(index_bytes) is not int
            or not 32768 <= index_bytes <= budget.bytes_limit
            or index_bytes % 4096
        ):
            raise ValueError("concrete host-bound native resources required")
        _private_directory(root)
        manifest_receipt = _receipt(root, "manifest.json", budget=budget, limit=MANIFEST_BYTES)
        if manifest_receipt["sha256"] != host.manifest_sha256:
            raise ValueError("native manifest differs from host ledger")
        manifest_raw = _read_verified(
            root,
            NativeFile(path="manifest.json", **manifest_receipt),
            budget=budget,
            limit=MANIFEST_BYTES,
        )
        manifest_data = strict_json(manifest_raw)
        if _canonical(manifest_data) != manifest_raw:
            raise ValueError("native manifest noncanonical JSON")
        manifest = NativeEvidenceManifest.model_validate(manifest_data)
        if manifest.state != host.state:
            raise ValueError("native manifest host state differs")
        _check_members(root, manifest)
        command_receipt = _receipt(root, manifest.command.path, budget=budget, limit=LINE_BYTES)
        if (
            command_receipt != manifest.command.model_dump(exclude={"path"})
            or command_receipt["sha256"] != host.command_sha256
        ):
            raise ValueError("native command differs from host ledger")
        raw_command = _read_verified(root, manifest.command, budget=budget, limit=LINE_BYTES)
        command_data = strict_json(raw_command)
        if _canonical(command_data) != raw_command:
            raise ValueError("native command JSON differs")
        command = NativeCommand.model_validate(command_data)
        budget.reserve(index_bytes)
        index = CapacityIndex(quota_bytes=index_bytes, row_bytes=4096)
        result = object.__new__(cls)
        result.root, result.host, result.budget = root, host, budget
        result.index_bytes, result.index = index_bytes, index
        result.manifest, result.command = manifest, command
        result._closed = False
        result._shard_identities = []
        try:
            result._verify_shards()
            result._verify_artifacts()
            return result
        except BaseException:
            result.close()
            raise

    def _verify_shards(self):
        expected = 1
        closed = False
        owner = tuple(getattr(self.command, key) for key in IDENTITY)
        for shard in self.manifest.shards:
            count = size = 0
            hashed = hashlib.sha256()
            with _parent(self.root, shard.path) as (directory_fd, name):
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
                )
                try:
                    before = _regular(descriptor)
                    if before.st_size != shard.size_bytes:
                        raise ValueError("native shard bytes differ")
                    with os.fdopen(os.dup(descriptor), "rb") as file:
                        while True:
                            offset = size
                            raw = file.readline(LINE_BYTES + 1)
                            if not raw:
                                break
                            size += len(raw)
                            if (
                                len(raw) >= LINE_BYTES
                                or not raw.endswith(b"\n")
                                or size > SHARD_BYTES
                            ):
                                raise ValueError("native shard line/byte bound differs")
                            hashed.update(raw)
                            data = strict_json(raw[:-1])
                            if _canonical(data) != raw[:-1]:
                                raise ValueError("native record noncanonical JSON")
                            row = NativeRecord.model_validate(data)
                            if (
                                row.sequence != expected
                                or tuple(getattr(row, key) for key in IDENTITY) != owner
                                or closed
                                or int(row.received_ns) > int(self.command.deadline_ns)
                            ):
                                raise ValueError(
                                    "native record owner/sequence/closure/deadline differs"
                                )
                            if row.observation.kind == "closed":
                                closed = True
                                if row.observation.records != row.sequence:
                                    raise ValueError("native closed record count differs")
                            locator = _canonical(
                                {
                                    "shard": shard.ordinal,
                                    "offset": offset,
                                    "bytes": len(raw),
                                    "sha256": hashlib.sha256(raw).hexdigest(),
                                }
                            )
                            self.budget.reserve(len(locator) * 2 + 256, rows=1)
                            self.index.append(
                                "native-record", locator, keys={"sequence": str(expected)}
                            )
                            expected += 1
                            count += 1
                    if _identity(before) != _identity(_regular(descriptor)) or _identity(
                        before
                    ) != _identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)):
                        raise ValueError("native shard changed during read")
                    self._shard_identities.append(_identity(before))
                finally:
                    os.close(descriptor)
            if (
                count != shard.records
                or size != shard.size_bytes
                or hashed.hexdigest() != shard.sha256
                or expected - 1 != shard.last_sequence
            ):
                raise ValueError("native shard descriptor differs")
        if expected - 1 != self.host.last_ack_sequence:
            raise ValueError("native host ACK boundary differs")
        if self.manifest.state == "complete":
            if not closed or self._last_status() != "observations-closed":
                raise ValueError("native successful terminal status absent")
        elif closed and self._last_status() != "failed":
            raise ValueError("native failed terminal status differs")
        self.structural_complete = closed and self.manifest.state == "complete"
        self.full_source_ready = False

    def _last_status(self):
        row = self.record(self.host.last_ack_sequence)
        return row.observation.outcome if row.observation.kind == "closed" else None

    def _artifact_bytes(self, row):
        self.budget.reserve(row.size_bytes * 4 + 65536, rows=1)
        result = bytearray()
        for ordinal, sequence in enumerate(row.chunk_sequences):
            observation = self.record(sequence).observation
            if row.purpose == "image":
                valid = observation.kind == "image" and observation.capture_id == row.artifact_id
            else:
                valid = (
                    observation.kind == "private-chunk"
                    and observation.purpose == row.purpose
                    and observation.artifact_id == row.artifact_id
                )
            if not valid or observation.chunk_index != ordinal:
                raise ValueError("native artifact indexed chunk owner differs")
            try:
                chunk = base64.b64decode(observation.data, validate=True)
            except binascii.Error as error:
                raise ValueError("native artifact indexed base64 differs") from error
            if len(result) + len(chunk) > row.size_bytes:
                raise ValueError("native artifact indexed byte bound differs")
            result.extend(chunk)
        if len(result) != row.size_bytes or hashlib.sha256(result).hexdigest() != row.sha256:
            raise ValueError("native artifact indexed bytes changed")
        return bytes(result)

    def _verify_artifacts(self):
        states = {}
        for row in self.manifest.artifacts:
            key = (row.purpose, row.artifact_id)
            if key in states:
                raise ValueError("duplicate native logical artifact")
            states[key] = {
                "row": row,
                "hash": hashlib.sha256(),
                "size": 0,
                "chunks": 0,
                "terminal": False,
            }
        for record in self.records():
            observation = record.observation
            if observation.kind in {"image", "private-chunk"}:
                purpose = "image" if observation.kind == "image" else observation.purpose
                artifact_id = (
                    observation.capture_id
                    if observation.kind == "image"
                    else observation.artifact_id
                )
                state = states.get((purpose, artifact_id))
                if (
                    state is None
                    or state["terminal"]
                    or observation.chunk_index != state["chunks"]
                    or state["chunks"] >= len(state["row"].chunk_sequences)
                    or state["row"].chunk_sequences[state["chunks"]] != record.sequence
                ):
                    raise ValueError("native artifact chunk owner/order differs")
                try:
                    chunk = base64.b64decode(observation.data, validate=True)
                except binascii.Error as error:
                    raise ValueError("native artifact chunk base64 differs") from error
                if (
                    not 0 < len(chunk) <= 49152
                    or base64.b64encode(chunk).decode() != observation.data
                    or state["size"] + len(chunk) > state["row"].size_bytes
                ):
                    raise ValueError("native artifact chunk bytes differ")
                state["hash"].update(chunk)
                state["size"] += len(chunk)
                state["chunks"] += 1
            elif observation.kind == "capture":
                state = states.get(("image", observation.capture_id))
                if (
                    state is None
                    or state["terminal"]
                    or state["row"].owner_sequence != record.sequence
                ):
                    raise ValueError("native capture artifact terminal differs")
                if (
                    state["size"] != observation.retained_bytes
                    or state["chunks"] != observation.chunks
                    or state["row"].sha256 != observation.retained_sha256
                ):
                    raise ValueError("native capture retained artifact differs")
                image = self._artifact_bytes(state["row"])
                if observation.retention == "full":
                    width, height = observation.width, observation.height
                else:
                    _, _, width, height = observation.crop_rect
                if validate_native_png(image, width=width, height=height) != observation.channels:
                    raise ValueError("native capture PNG channels differ")
                state["terminal"] = True
            elif observation.kind == "trace-completion":
                state = states.get(("native-trace", observation.artifact_id))
                if (
                    state is None
                    or state["terminal"]
                    or state["row"].owner_sequence != record.sequence
                ):
                    raise ValueError("native trace artifact terminal differs")
                validate_native_trace_completion(
                    record,
                    clock_id=self.command.clock_id,
                    deadline_ns=int(self.command.deadline_ns),
                    retained_bytes=state["size"],
                    retained_chunks=state["chunks"],
                    retained_sha256=state["row"].sha256,
                )
                state["terminal"] = True
        for state in states.values():
            row = state["row"]
            if (
                state["size"] != row.size_bytes
                or state["chunks"] != len(row.chunk_sequences)
                or state["hash"].hexdigest() != row.sha256
                or (row.purpose != "rejected-capture" and not state["terminal"])
            ):
                raise ValueError("native artifact original/chunk closure differs")
        if self.manifest.failure is not None:
            row = self.manifest.failure
            if _receipt(
                self.root, row.path, budget=self.budget, limit=MANIFEST_BYTES
            ) != row.model_dump(exclude={"path"}):
                raise ValueError("native failure snapshot descriptor differs")
            snapshot = NativeFailureSnapshot.model_validate(
                strict_json(
                    _read_verified(self.root, row, budget=self.budget, limit=MANIFEST_BYTES)
                )
            )
            if tuple(getattr(snapshot, key) for key in IDENTITY) != tuple(
                getattr(self.command, key) for key in IDENTITY
            ):
                raise ValueError("native failure snapshot owner differs")
            if (
                snapshot.failure_ns is None
                or snapshot.retention_deadline_ns is None
                or int(snapshot.retention_deadline_ns) != int(snapshot.failure_ns) + 10_000_000_000
                or int(snapshot.observed_ns) < int(snapshot.failure_ns)
            ):
                raise ValueError("native failure retention clock differs")
            operations = {operation.operation_id: operation for operation in snapshot.operations}
            if len(operations) != len(snapshot.operations):
                raise ValueError("native failure operation duplicate")
            for operation in snapshot.operations:
                if (
                    int(operation.started_ns) > int(snapshot.observed_ns)
                    or (operation.state == "pending") != (operation.settled_ns is None)
                    or (
                        operation.settled_ns is not None
                        and not int(operation.started_ns)
                        <= int(operation.settled_ns)
                        <= int(snapshot.observed_ns)
                    )
                ):
                    raise ValueError("native failure pending operation boundary differs")
            if len({handle.operation_id for handle in snapshot.handles}) != len(
                snapshot.handles
            ) or any(handle.operation_id not in operations for handle in snapshot.handles):
                raise ValueError("native failure late handle ownership differs")
            expected_disposition = (
                "pending"
                if any(operation.state == "pending" for operation in snapshot.operations)
                else "partial"
                if snapshot.discarded_bytes or snapshot.artifacts or snapshot.handles
                else "settled"
            )
            if snapshot.disposition != expected_disposition:
                raise ValueError("native failure pending disposition differs")
            retained = {artifact.artifact_id: artifact for artifact in snapshot.artifacts}
            if (
                len(retained) != len(snapshot.artifacts)
                or sum(
                    artifact.retained_bytes - artifact.acknowledged_bytes
                    for artifact in snapshot.artifacts
                )
                != snapshot.held_bytes
            ):
                raise ValueError("native failure retained owner totals differ")
            # drainFailureEvidence emits separate byte/ACK receipts outside
            # NativeRecord. Until C3b supplies their durable ledger schema,
            # neither a partial ACK nor an unretained snapshot artifact can
            # be promoted from this NDJSON/binary inventory.
            if any(artifact.acknowledged_bytes for artifact in snapshot.artifacts):
                raise ValueError("native separate failure ACK ledger absent")
            if set(retained) != {
                artifact_id for purpose, artifact_id in states if purpose == "rejected-capture"
            }:
                raise ValueError("native failure snapshot artifact closure differs")
            for artifact in snapshot.artifacts:
                if (
                    artifact.retained_bytes > artifact.observed_bytes
                    or artifact.acknowledged_bytes > artifact.retained_bytes
                    or int(artifact.received_ns) > int(snapshot.observed_ns)
                ):
                    raise ValueError("native failure artifact receipt differs")
            for (purpose, artifact_id), state in states.items():
                if purpose == "rejected-capture":
                    artifact = retained.get(artifact_id)
                    if (
                        artifact is None
                        or artifact.retained_bytes != state["size"]
                        or artifact.sha256 != state["row"].sha256
                    ):
                        raise ValueError("native failure artifact ownership differs")

    def record(self, sequence):
        if self._closed:
            raise ValueError("native raw session closed")
        raw = self.index.find("native-record", "sequence", str(sequence))
        if raw is None:
            raise IndexError(sequence)
        locator = strict_json(raw)
        shard = self.manifest.shards[locator["shard"]]
        with _parent(self.root, shard.path) as (directory_fd, name):
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
            )
            try:
                before = _regular(descriptor)
                if _identity(before) != self._shard_identities[locator["shard"]]:
                    raise ValueError("native indexed original identity changed")
                os.lseek(descriptor, locator["offset"], os.SEEK_SET)
                line = os.read(descriptor, locator["bytes"])
                if (
                    len(line) != locator["bytes"]
                    or hashlib.sha256(line).hexdigest() != locator["sha256"]
                    or _identity(before) != _identity(_regular(descriptor))
                    or _identity(before)
                    != _identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
                ):
                    raise ValueError("native indexed original changed")
            finally:
                os.close(descriptor)
        return NativeRecord.model_validate(strict_json(line[:-1]))

    def records(self):
        for sequence in range(1, self.host.last_ack_sequence + 1):
            yield self.record(sequence)

    def copy(self, destination):
        if self._closed:
            raise ValueError("native raw session closed")
        manifest_receipt = _receipt(
            self.root, "manifest.json", budget=self.budget, limit=MANIFEST_BYTES
        )
        if manifest_receipt["sha256"] != self.host.manifest_sha256:
            raise ValueError("native manifest changed before copy")

        def members():
            yield "manifest.json", manifest_receipt
            for row in chain((self.manifest.command,), self.manifest.shards):
                yield row.path, row.model_dump(include={"sha256", "size_bytes"})
            if self.manifest.failure is not None:
                yield (
                    self.manifest.failure.path,
                    self.manifest.failure.model_dump(include={"sha256", "size_bytes"}),
                )

        copy_private(
            self.root,
            destination,
            members(),
            budget=self.budget,
        )
        return type(self).open(
            destination, host=self.host, budget=self.budget, index_bytes=self.index_bytes
        )

    def close(self):
        if not self._closed:
            self.index.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
