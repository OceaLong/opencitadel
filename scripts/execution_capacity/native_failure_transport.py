"""Bounded host ACK spool for native failure callbacks, outside NativeRecord.

This module retains the callback prefix only. Final snapshot/manifest promotion
requires the separately reviewed two-way failure closure and is not provided
by a successful callback ACK alone.
"""

import base64
import binascii
import hashlib
import os
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBytes, model_validator
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import (
    NativeCommand,
    NativeFailureSnapshotV2,
    NativeNS,
    NativeRecord,
)
from scripts.acceptance.capacity_models import (
    NativeFailureSourceRecordRef as NativeRecordSourceRef,
)
from scripts.acceptance.capacity_records import ID, Digest, Nat, Pos, Record
from scripts.execution_capacity.attempt import AttemptLedger, digest
from scripts.execution_capacity.attempt import encode as attempt_encode
from scripts.execution_capacity.native_raw import (
    IDENTITY,
    LINE_BYTES,
    MAX_RECORDS,
    SHARD_BYTES,
    _canonical,
)
from scripts.execution_capacity.native_transport import (
    NativeHostWriter,
    _digest,
    _private_size,
    _sync_directory,
    _write_exact,
)
from scripts.execution_capacity.ownership import _open_private, _private_directory

FAILURE_RAW_BYTES = 128 * 1024 * 1024
FAILURE_CHUNKS = 4096
FAILURE_FILE_BYTES = FAILURE_CHUNKS * (LINE_BYTES - 1)
FAILURE_LEDGER_ROW_BYTES = 1536
FAILURE_LEDGER_BYTES = (FAILURE_CHUNKS + 1) * FAILURE_LEDGER_ROW_BYTES


class FailureOpenRow(Record):
    schema_id: Literal["opencitadel.native-failure-open.v1"] = Field(alias="schema")
    command_sha256: Digest
    host_clock_digest: Digest
    file: Literal["failure-drain.ndjson"]


class FailureOpenRowV2(FailureOpenRow):
    schema_id: Literal["opencitadel.native-failure-open.v2"] = Field(alias="schema")
    callback_wire_version: Literal[2]


class IndependentSourceRef(Record):
    kind: Literal["independent"]


SourceRef = Annotated[IndependentSourceRef | NativeRecordSourceRef, Field(discriminator="kind")]


class FailureAckRow(Record):
    schema_id: Literal["opencitadel.native-failure-ack.v1"] = Field(alias="schema")
    command_sha256: Digest
    host_clock_digest: Digest
    sequence: Pos
    offset: Nat
    bytes: Annotated[Pos, Field(lt=LINE_BYTES)]
    sha256: Digest
    artifact_id: ID
    artifact_offset: Nat
    chunk_bytes: Annotated[Pos, Field(le=49152)]
    chunk_sha256: Digest
    host_ack_ns: Nat


class FailureAckRowV2(FailureAckRow):
    schema_id: Literal["opencitadel.native-failure-ack.v2"] = Field(alias="schema")
    source_record_ref: SourceRef


class FailureCallback(Record):
    wire_version: Literal[1]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    artifact_id: ID
    observed_bytes: Nat
    retained_bytes: Annotated[Pos, Field(le=FAILURE_RAW_BYTES)]
    retained_sha256: Digest
    artifact_received_ns: NativeNS
    failure_ns: NativeNS
    retention_deadline_ns: NativeNS
    offset: Nat
    bytes: Annotated[Pos, Field(le=49152)]
    sha256: Digest
    data: StrictBytes

    @model_validator(mode="after")
    def exact_chunk(self):
        self.artifact_id.encode("utf-8", errors="strict")
        if (
            self.observed_bytes < self.retained_bytes
            or self.offset + self.bytes > self.retained_bytes
            or len(self.data) != self.bytes
            or _digest(self.data) != self.sha256
            or int(self.retention_deadline_ns) != int(self.failure_ns) + 10_000_000_000
            or int(self.artifact_received_ns) > int(self.retention_deadline_ns)
        ):
            raise ValueError("native failure callback byte/clock closure differs")
        return self


class FailureCallbackV2(FailureCallback):
    wire_version: Literal[2]
    source_record_ref: SourceRef

    @model_validator(mode="after")
    def exact_source_range(self):
        ref = self.source_record_ref
        if isinstance(ref, NativeRecordSourceRef) and (
            ref.artifact_offset != self.offset or ref.bytes != self.bytes
        ):
            raise ValueError("native failure source range differs from callback")
        return self


class FailureDrainLine(Record):
    schema_id: Literal["opencitadel.native-failure-drain.v1"] = Field(alias="schema")
    wire_version: Literal[1]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    artifact_id: ID
    observed_bytes: Nat
    retained_bytes: Annotated[Pos, Field(le=FAILURE_RAW_BYTES)]
    retained_sha256: Digest
    artifact_received_ns: NativeNS
    failure_ns: NativeNS
    retention_deadline_ns: NativeNS
    offset: Nat
    bytes: Annotated[Pos, Field(le=49152)]
    sha256: Digest
    data: str
    host_recorded_ns: NativeNS

    @model_validator(mode="after")
    def exact_line(self):
        try:
            raw = base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("native failure callback base64 differs") from error
        if (
            base64.b64encode(raw).decode() != self.data
            or len(raw) != self.bytes
            or _digest(raw) != self.sha256
            or self.offset + self.bytes > self.retained_bytes
            or int(self.host_recorded_ns)
            < max(int(self.failure_ns), int(self.artifact_received_ns))
            or int(self.host_recorded_ns) > int(self.retention_deadline_ns)
            or int(self.retention_deadline_ns) != int(self.failure_ns) + 10_000_000_000
        ):
            raise ValueError("native failure drain line differs")
        return self


class FailureDrainLineV2(FailureDrainLine):
    schema_id: Literal["opencitadel.native-failure-drain.v2"] = Field(alias="schema")
    wire_version: Literal[2]
    source_record_ref: SourceRef

    @model_validator(mode="after")
    def exact_source_range(self):
        ref = self.source_record_ref
        if isinstance(ref, NativeRecordSourceRef) and (
            ref.artifact_offset != self.offset or ref.bytes != self.bytes
        ):
            raise ValueError("native failure source range differs from line")
        return self


class NativeRecordAck(Record):
    command_sha256: Digest
    host_clock_digest: Digest
    sequence: Annotated[Pos, Field(le=MAX_RECORDS)]
    shard: Nat
    offset: Nat
    bytes: Annotated[Pos, Field(lt=LINE_BYTES)]
    sha256: Digest
    producer_received_ns: NativeNS
    host_ack_ns: Nat


def _artifact_metadata(row):
    return (
        row.observed_bytes,
        row.retained_bytes,
        row.retained_sha256,
        row.artifact_received_ns,
        row.failure_ns,
        row.retention_deadline_ns,
    )


def _preflight_attempt_row(ledger, kind, body):
    row = {
        "sequence": len(ledger.rows) + 1,
        "previous": ledger.chain,
        "kind": kind,
        "body": body,
    }
    raw = attempt_encode({**row, "digest": digest(row)}) + b"\n"
    if len(raw) > FAILURE_LEDGER_ROW_BYTES:
        raise ValueError("native failure ACK ledger row bound exceeded")
    return len(raw)


def _verify_record_source_bytes(
    record: NativeRecord, ref: NativeRecordSourceRef, callback_bytes: bytes
) -> None:
    observation = record.observation
    if (
        observation.kind != "private-chunk"
        or observation.purpose != ref.purpose
        or observation.artifact_id != ref.artifact_id
        or observation.chunk_index != ref.chunk_index
    ):
        raise ValueError("native failure source record identity differs")
    try:
        raw = base64.b64decode(observation.data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("native failure source record base64 differs") from error
    if (
        not 0 < len(raw) <= 49152
        or base64.b64encode(raw).decode() != observation.data
        or len(raw) != ref.bytes
        or raw != callback_bytes
    ):
        raise ValueError("native failure source record bytes differ")


def _verified_record_line(root: Path, command: NativeCommand, ack: NativeRecordAck) -> NativeRecord:
    path = root / "records" / f"{ack.shard:06}.ndjson"
    with os.fdopen(_open_private(path, os.O_RDONLY), "rb") as stream:
        stream.seek(ack.offset)
        line = stream.read(ack.bytes)
    if len(line) != ack.bytes or _digest(line) != ack.sha256 or not line.endswith(b"\n"):
        raise ValueError("native failure source ACKed line changed")
    data = strict_json(line[:-1])
    record = NativeRecord.model_validate(data)
    if (
        _canonical(data) != line[:-1]
        or record.sequence != ack.sequence
        or record.received_ns != ack.producer_received_ns
        or any(getattr(record, name) != getattr(command, name) for name in IDENTITY)
    ):
        raise ValueError("native failure source ACKed record identity differs")
    return record


def _verified_record_prefix(
    ledger: AttemptLedger,
    root: Path,
    command: NativeCommand,
    command_sha256: str,
    clock_digest: str,
    before_sequence: int,
    refs: dict[int, NativeRecordSourceRef],
    *,
    verify_all: bool = True,
    on_record=None,
    fixture_limits=None,
) -> dict[int, NativeRecordAck]:
    """Read the exact durable record-ACK prefix, including every shard EOF."""
    if on_record is not None and not verify_all:
        raise ValueError("native failure record visitor requires full physical verification")
    record_root = root / "records"
    _private_directory(record_root)
    rows = [
        row
        for row in ledger.records("native-record-ack")
        if row["body"].get("command_sha256") == command_sha256
    ]
    if len(rows) > MAX_RECORDS:
        raise ValueError("native failure source record count exceeds bound")
    if len(refs) > FAILURE_CHUNKS:
        raise ValueError("native failure source reference count exceeds bound")
    selected = {}
    expected_files = set()
    previous_shard = expected_offset = previous_host_ns = total_bytes = 0
    for sequence, ledger_row in enumerate(rows, 1):
        ack = NativeRecordAck.model_validate(ledger_row["body"])
        if fixture_limits is not None:
            total_bytes += ack.bytes
            if (
                ack.shard >= fixture_limits["shards"]
                or ack.offset + ack.bytes > fixture_limits["shard"]
                or total_bytes > fixture_limits["native_total"]
            ):
                raise ValueError("failed diagnostic record ACK exceeds fixture bound")
        if (
            ledger_row["sequence"] >= before_sequence
            or ack.sequence != sequence
            or ack.command_sha256 != command_sha256
            or ack.host_clock_digest != clock_digest
            or (sequence == 1 and ack.shard != 0)
            or ack.shard not in {previous_shard, previous_shard + 1}
            or ack.host_ack_ns < int(ack.producer_received_ns)
            or ack.host_ack_ns < previous_host_ns
            or ack.host_ack_ns > int(command.deadline_ns)
        ):
            raise ValueError("native failure source ACK owner/order/clock differs")
        if ack.shard != previous_shard:
            old_path = record_root / f"{previous_shard:06}.ndjson"
            if _private_size(old_path) != expected_offset:
                raise ValueError("native failure source unacknowledged shard suffix")
            previous_shard, expected_offset = ack.shard, 0
        if ack.offset != expected_offset or expected_offset + ack.bytes > SHARD_BYTES:
            raise ValueError("native failure source ACK shard offset differs")
        path = record_root / f"{ack.shard:06}.ndjson"
        expected_files.add(path.name)
        if verify_all:
            record = _verified_record_line(root, command, ack)
            if on_record is not None:
                on_record(record)
        if sequence in refs:
            selected[sequence] = ack
        expected_offset += ack.bytes
        previous_host_ns = ack.host_ack_ns
    if rows and _private_size(record_root / f"{previous_shard:06}.ndjson") != expected_offset:
        raise ValueError("native failure source unacknowledged shard suffix")
    if {path.name for path in record_root.iterdir()} != expected_files:
        raise ValueError("native failure source unacknowledged shard file")
    if selected.keys() != refs.keys():
        raise ValueError("native failure source record lacks durable ACK")
    return selected


class NativeFailureDrainWriter:
    """One command's callback bytes, with an independent fsynced ACK per chunk."""

    @classmethod
    def create(cls, native: NativeHostWriter, *, wire_version: Literal[1, 2] = 1):
        if type(native) is not NativeHostWriter:
            raise ValueError("owned unfinished native command required")
        with native.ledger.thread_lock:
            return cls._create_locked(native, wire_version=wire_version)

    @classmethod
    def _create_locked(cls, native: NativeHostWriter, *, wire_version: Literal[1, 2]):
        if native.finished or native.poisoned or native.ledger.poisoned or native.ledger.closed:
            raise ValueError("owned unfinished native command required")
        if type(wire_version) is not int or wire_version not in (1, 2):
            raise ValueError("unsupported native failure callback version")
        _private_directory(native.root)
        result = object.__new__(cls)
        result.native = native
        result.root, result.ledger, result.command = native.root, native.ledger, native.command
        result.key, result.clock_digest = native.key, native.clock_digest
        result.wire_version = wire_version
        result.path = native.root / "failure-drain.ndjson"
        result.fd = None
        result.poisoned = False
        result.count = result.file_bytes = result.raw_bytes = result.ledger_bytes = 0
        result.artifacts = {}
        result.source_sequences = set()
        result.failure_clock = None
        try:
            if wire_version == 2:
                _verified_record_prefix(
                    native.ledger,
                    native.root,
                    native.command,
                    native.key,
                    native.clock_digest,
                    len(native.ledger.rows) + 1,
                    {},
                )
            result.fd = _open_private(result.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            _sync_directory(native.root)
            open_body = {
                "schema": f"opencitadel.native-failure-open.v{wire_version}",
                "command_sha256": native.key,
                "host_clock_digest": native.clock_digest,
                "file": result.path.name,
            }
            if wire_version == 2:
                open_body["callback_wire_version"] = 2
            body = (
                (FailureOpenRowV2 if wire_version == 2 else FailureOpenRow)
                .model_validate(open_body)
                .model_dump(by_alias=True)
            )
            with native.ledger.thread_lock:
                result.ledger_bytes = _preflight_attempt_row(
                    native.ledger, "native-failure-open", body
                )
                native.ledger.append("native-failure-open", body)
                result.open_sequence = len(native.ledger.rows)
            return result
        except BaseException:
            result.poisoned = True
            result.close()
            raise

    def append_chunk(self, chunk: dict):
        with self.ledger.thread_lock:
            return self._append_chunk_locked(chunk)

    def _append_chunk_locked(self, chunk: dict):
        if (
            self.poisoned
            or self.fd is None
            or self.ledger.poisoned
            or self.ledger.closed
            or self.native.finished
            or self.native.poisoned
        ):
            raise ValueError("native failure writer closed or uncertain")
        try:
            callback = (
                FailureCallbackV2 if self.wire_version == 2 else FailureCallback
            ).model_validate(chunk)
            if any(getattr(callback, name) != getattr(self.command, name) for name in IDENTITY):
                raise ValueError("native failure callback foreign command")
            prior = self.artifacts.get(callback.artifact_id)
            if prior is None:
                if callback.offset != 0 or len(self.artifacts) >= 128:
                    raise ValueError("native failure callback first offset/count differs")
            elif prior["offset"] != callback.offset or prior["metadata"] != _artifact_metadata(
                callback
            ):
                raise ValueError("native failure callback offset/metadata differs")
            failure_clock = (callback.failure_ns, callback.retention_deadline_ns)
            if self.failure_clock is not None and failure_clock != self.failure_clock:
                raise ValueError("native failure callback clock changed")
            next_hash = prior["hash"].copy() if prior is not None else hashlib.sha256()
            next_hash.update(callback.data)
            if (
                callback.offset + callback.bytes == callback.retained_bytes
                and next_hash.hexdigest() != callback.retained_sha256
            ):
                raise ValueError("native failure fully ACKed artifact digest differs")
            if self.count >= FAILURE_CHUNKS or self.raw_bytes + callback.bytes > FAILURE_RAW_BYTES:
                raise ValueError("native failure cumulative ACK quota exceeded")
            if self.wire_version == 2:
                ref = callback.source_record_ref
                refs = {ref.sequence: ref} if isinstance(ref, NativeRecordSourceRef) else {}
                if refs and ref.sequence in self.source_sequences:
                    raise ValueError("native failure source record reused")
                source = _verified_record_prefix(
                    self.ledger,
                    self.root,
                    self.command,
                    self.key,
                    self.clock_digest,
                    self.open_sequence,
                    refs,
                    verify_all=False,
                )
                if refs:
                    _verify_record_source_bytes(
                        _verified_record_line(self.root, self.command, source[ref.sequence]),
                        ref,
                        callback.data,
                    )
            recorded_ns = time.monotonic_ns()
            line_model = (FailureDrainLineV2 if self.wire_version == 2 else FailureDrainLine)(
                schema=f"opencitadel.native-failure-drain.v{self.wire_version}",
                **callback.model_dump(exclude={"data"}),
                data=base64.b64encode(callback.data).decode(),
                host_recorded_ns=str(recorded_ns),
            )
            line = _canonical(line_model.model_dump(by_alias=True)) + b"\n"
            if len(line) >= LINE_BYTES or self.file_bytes + len(line) > FAILURE_FILE_BYTES:
                raise ValueError("native failure callback encoded spool quota exceeded")
            body = {
                "schema": f"opencitadel.native-failure-ack.v{self.wire_version}",
                "command_sha256": self.key,
                "host_clock_digest": self.clock_digest,
                "sequence": self.count + 1,
                "offset": self.file_bytes,
                "bytes": len(line),
                "sha256": _digest(line),
                "artifact_id": callback.artifact_id,
                "artifact_offset": callback.offset,
                "chunk_bytes": callback.bytes,
                "chunk_sha256": callback.sha256,
                "host_ack_ns": 99_999_999_999_999_999_999,
            }
            if self.wire_version == 2:
                body["source_record_ref"] = callback.source_record_ref.model_dump()
            ack_type = FailureAckRowV2 if self.wire_version == 2 else FailureAckRow
            with self.ledger.thread_lock:
                body = ack_type.model_validate(body).model_dump(by_alias=True)
                max_ledger_row_bytes = _preflight_attempt_row(
                    self.ledger, "native-failure-ack", body
                )
                if self.ledger_bytes + max_ledger_row_bytes > FAILURE_LEDGER_BYTES:
                    raise ValueError("native failure ACK ledger quota exceeded")
                _write_exact(self.fd, line)
                ack_ns = time.monotonic_ns()
                if ack_ns < recorded_ns or ack_ns > int(callback.retention_deadline_ns):
                    raise ValueError("native failure host ACK missed retention deadline")
                body["host_ack_ns"] = ack_ns
                body = ack_type.model_validate(body).model_dump(by_alias=True)
                ledger_row_bytes = _preflight_attempt_row(self.ledger, "native-failure-ack", body)
                receipt_id = self.ledger.append("native-failure-ack", body)
            self.count += 1
            self.file_bytes += len(line)
            self.raw_bytes += callback.bytes
            self.ledger_bytes += ledger_row_bytes
            self.failure_clock = failure_clock
            if self.wire_version == 2 and refs:
                self.source_sequences.add(ref.sequence)
            if prior is None:
                self.artifacts[callback.artifact_id] = {
                    "metadata": _artifact_metadata(callback),
                    "offset": callback.bytes,
                    "hash": next_hash,
                }
            else:
                prior["offset"] += callback.bytes
                prior["hash"] = next_hash
            return {
                **callback.model_dump(exclude={"data"}),
                "durable_receipt_id": receipt_id,
            }
        except BaseException:
            self.poisoned = True
            raise

    def close(self):
        with self.ledger.thread_lock:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _reopen_failure_prefix(
    ledger_root: Path, plan: dict, command_sha256: str, *, copy_root=None, include_proof=False
):
    """Verify only the independently ACKed prefix; no failure snapshot authority."""
    with AttemptLedger.open(ledger_root, plan) as ledger:
        return _reopen_failure_prefix_on_ledger(
            ledger, command_sha256, copy_root=copy_root, include_proof=include_proof
        )


def _reopen_failure_prefix_on_ledger(
    ledger: AttemptLedger,
    command_sha256: str,
    *,
    copy_root=None,
    include_proof=False,
    physical_location: Path | None = None,
    fixture_limits=None,
):
    """Borrow one already locked attempt for an atomic failed-close verification."""
    from scripts.execution_capacity.native_transport import (
        _require_registered_command,
        _require_registered_command_at,
    )

    if physical_location is None:
        _require_registered_command(ledger, command_sha256)
    else:
        if copy_root is not None:
            raise ValueError("one explicit physical native source required")
        _require_registered_command_at(ledger.plan, physical_location, command_sha256)
    clocks = ledger.records("host-clock")
    command_rows = [
        r
        for r in ledger.records("native-command")
        if r["body"].get("command_sha256") == command_sha256
    ]
    opens = [
        r
        for r in ledger.records("native-failure-open")
        if r["body"].get("command_sha256") == command_sha256
    ]
    acks = [
        r
        for r in ledger.records("native-failure-ack")
        if r["body"].get("command_sha256") == command_sha256
    ]
    if len(acks) > FAILURE_CHUNKS:
        raise ValueError("native failure cumulative spool quota exceeded")
    if len(clocks) != 1 or len(command_rows) != 1 or len(opens) != 1:
        raise ValueError("unique native failure host owner absent")
    command = command_rows[0]["body"]
    open_schema = opens[0]["body"].get("schema")
    if open_schema not in {
        "opencitadel.native-failure-open.v1",
        "opencitadel.native-failure-open.v2",
    }:
        raise ValueError("native failure open version differs")
    wire_version = 2 if open_schema.endswith(".v2") else 1
    opened = (
        (FailureOpenRowV2 if wire_version == 2 else FailureOpenRow)
        .model_validate(opens[0]["body"])
        .model_dump(by_alias=True)
    )
    if (
        not clocks[0]["sequence"] < command_rows[0]["sequence"] < opens[0]["sequence"]
        or digest(clocks[0]["body"]) != command["host_clock_digest"]
        or command["command_sha256"] != command_sha256
        or command["root"] != f"native-{command_sha256}"
        or opened["host_clock_digest"] != command["host_clock_digest"]
        or opened["file"] != "failure-drain.ndjson"
    ):
        raise ValueError("native failure host clock/command/open differs")
    root = (
        copy_root
        if copy_root is not None
        else (ledger.root if physical_location is None else physical_location) / command["root"]
    )
    _private_directory(root)
    with os.fdopen(_open_private(root / "command.json", os.O_RDONLY), "rb") as command_file:
        command_raw = command_file.read(LINE_BYTES)
    if (
        len(command_raw) != command["size_bytes"]
        or _digest(command_raw) != command_sha256
        or _canonical(strict_json(command_raw)) != command_raw
    ):
        raise ValueError("native failure command bytes differ")
    command_model = NativeCommand.model_validate(strict_json(command_raw))
    record_sources = {}
    if wire_version == 2:
        for ack_row in acks:
            ack = FailureAckRowV2.model_validate(ack_row["body"])
            ref = ack.source_record_ref
            if isinstance(ref, NativeRecordSourceRef):
                if ref.sequence in record_sources:
                    raise ValueError("native failure source record reused")
                record_sources[ref.sequence] = ref
        source_locators = _verified_record_prefix(
            ledger,
            root,
            command_model,
            command_sha256,
            command["host_clock_digest"],
            opens[0]["sequence"],
            record_sources,
            fixture_limits=fixture_limits,
        )
    path = root / opened["file"]
    if fixture_limits is not None and _private_size(path) > fixture_limits["drain"]:
        raise ValueError("failed diagnostic drain exceeds fixture bound")
    states = {}
    file_offset = raw_total = 0
    ledger_bytes = len(attempt_encode({**opens[0], "digest": digest(opens[0])})) + 1
    if ledger_bytes > FAILURE_LEDGER_ROW_BYTES:
        raise ValueError("native failure open ledger row bound exceeded")
    failure_clock = None
    previous_host_ack_ns = 0
    with os.fdopen(_open_private(path, os.O_RDONLY), "rb") as stream:
        for sequence, ack_row in enumerate(acks, 1):
            ack = (
                (FailureAckRowV2 if wire_version == 2 else FailureAckRow)
                .model_validate(ack_row["body"])
                .model_dump(by_alias=True)
            )
            encoded_ack_bytes = len(attempt_encode({**ack_row, "digest": digest(ack_row)})) + 1
            ledger_bytes += encoded_ack_bytes
            if (
                ack_row["sequence"] <= opens[0]["sequence"]
                or ack["sequence"] != sequence
                or ack["command_sha256"] != command_sha256
                or ack["host_clock_digest"] != command["host_clock_digest"]
                or ack["offset"] != file_offset
                or not 0 < ack["bytes"] < LINE_BYTES
                or encoded_ack_bytes > FAILURE_LEDGER_ROW_BYTES
                or ledger_bytes > FAILURE_LEDGER_BYTES
                or ack["host_ack_ns"] < previous_host_ack_ns
                or (
                    fixture_limits is not None
                    and file_offset + ack["bytes"] > fixture_limits["drain"]
                )
            ):
                raise ValueError("native failure host ACK order/owner differs")
            line = stream.read(ack["bytes"])
            if (
                len(line) != ack["bytes"]
                or _digest(line) != ack["sha256"]
                or not line.endswith(b"\n")
            ):
                raise ValueError("native failure ACKed line changed")
            data = strict_json(line[:-1])
            if _canonical(data) != line[:-1]:
                raise ValueError("native failure ACKed line noncanonical")
            row = (FailureDrainLineV2 if wire_version == 2 else FailureDrainLine).model_validate(
                data
            )
            current_clock = (row.failure_ns, row.retention_deadline_ns)
            if failure_clock is not None and current_clock != failure_clock:
                raise ValueError("native failure ACKed clock changed")
            failure_clock = current_clock
            if (
                row.artifact_id != ack["artifact_id"]
                or row.offset != ack["artifact_offset"]
                or row.bytes != ack["chunk_bytes"]
                or row.sha256 != ack["chunk_sha256"]
                or int(row.host_recorded_ns) > ack["host_ack_ns"]
                or ack["host_ack_ns"] > int(row.retention_deadline_ns)
                or any(getattr(row, name) != getattr(command_model, name) for name in IDENTITY)
                or (
                    wire_version == 2
                    and row.source_record_ref.model_dump() != ack["source_record_ref"]
                )
            ):
                raise ValueError("native failure ACKed callback differs")
            prior = states.get(row.artifact_id)
            if prior is None:
                if row.offset != 0 or len(states) >= 128:
                    raise ValueError("native failure artifact first offset/count differs")
                prior = {
                    "metadata": _artifact_metadata(row),
                    "offset": 0,
                    "hash": hashlib.sha256(),
                    "source_record_refs": [],
                }
                states[row.artifact_id] = prior
            if prior["offset"] != row.offset or prior["metadata"] != _artifact_metadata(row):
                raise ValueError("native failure artifact ACK prefix differs")
            raw = base64.b64decode(row.data, validate=True)
            if wire_version == 2 and isinstance(row.source_record_ref, NativeRecordSourceRef):
                ref = row.source_record_ref
                _verify_record_source_bytes(
                    _verified_record_line(root, command_model, source_locators[ref.sequence]),
                    ref,
                    raw,
                )
                prior["source_record_refs"].append(ref.model_dump())
            prior["hash"].update(raw)
            prior["offset"] += row.bytes
            if (
                prior["offset"] == row.retained_bytes
                and prior["hash"].hexdigest() != row.retained_sha256
            ):
                raise ValueError("native failure complete artifact bytes differ")
            raw_total += row.bytes
            file_offset += len(line)
            previous_host_ack_ns = ack["host_ack_ns"]
        if stream.read(1):
            raise ValueError("native failure unacknowledged file suffix retained")
    if (
        raw_total > FAILURE_RAW_BYTES
        or len(acks) > FAILURE_CHUNKS
        or file_offset > FAILURE_FILE_BYTES
    ):
        raise ValueError("native failure cumulative spool quota exceeded")
    if _private_size(path) != file_offset:
        raise ValueError("native failure spool changed during read")
    artifacts = {
        artifact_id: {
            "metadata": state["metadata"],
            "acknowledged_bytes": state["offset"],
            "acknowledged_sha256": state["hash"].hexdigest(),
            **({"source_record_refs": state["source_record_refs"]} if include_proof else {}),
        }
        for artifact_id, state in states.items()
    }
    if include_proof:
        return {
            "wire_version": wire_version,
            "command": command_model,
            "failure_clock": failure_clock,
            "failure_ack_count": len(acks),
            "failure_open_digest": digest(opens[0]),
            "failure_last_ack_digest": digest(acks[-1]) if acks else digest(opens[0]),
            "artifacts": artifacts,
        }
    return artifacts


def reopen_failure_prefix(ledger_root: Path, plan: dict, command_sha256: str, *, copy_root=None):
    """Preserved v1/v2 prefix summary API; final snapshot authority is separate."""
    return _reopen_failure_prefix(ledger_root, plan, command_sha256, copy_root=copy_root)


def reconcile_failure_snapshot_v2(
    ledger_root: Path, plan: dict, command_sha256: str, snapshot_raw: bytes
):
    """Pure diagnostic comparison; does not write a close or authorize a reader."""
    snapshot = _parse_failure_snapshot_v2(snapshot_raw)
    proof = _reopen_failure_prefix(ledger_root, plan, command_sha256, include_proof=True)
    return _reconcile_failure_snapshot_v2_from_proof(snapshot_raw, snapshot, proof)


def _reconcile_failure_snapshot_v2_on_ledger(
    ledger: AttemptLedger,
    command_sha256: str,
    snapshot_raw: bytes,
    *,
    physical_location: Path | None = None,
    fixture_limits=None,
):
    """Borrow a locked attempt; never nested-open its nonblocking flock."""
    snapshot = _parse_failure_snapshot_v2(snapshot_raw)
    proof = _reopen_failure_prefix_on_ledger(
        ledger,
        command_sha256,
        include_proof=True,
        physical_location=physical_location,
        fixture_limits=fixture_limits,
    )
    return _reconcile_failure_snapshot_v2_from_proof(snapshot_raw, snapshot, proof)


def _parse_failure_snapshot_v2(snapshot_raw: bytes):
    if type(snapshot_raw) is not bytes or not 0 < len(snapshot_raw) <= 8 * 1024 * 1024:
        raise ValueError("bounded exact native failure snapshot bytes required")
    data = strict_json(snapshot_raw)
    if _canonical(data) != snapshot_raw:
        raise ValueError("native failure snapshot noncanonical JSON")
    return NativeFailureSnapshotV2.model_validate(data)


def _reconcile_failure_snapshot_v2_from_proof(snapshot_raw, snapshot, proof):
    if proof["wire_version"] != 2:
        raise ValueError("native failure snapshot requires wire2 ACK prefix")
    if any(getattr(snapshot, name) != getattr(proof["command"], name) for name in IDENTITY):
        raise ValueError("native failure snapshot foreign command")
    if (
        int(snapshot.retention_deadline_ns) != int(snapshot.failure_ns) + 10_000_000_000
        or int(snapshot.observed_ns) < int(snapshot.failure_ns)
        or (
            proof["failure_clock"] is not None
            and proof["failure_clock"] != (snapshot.failure_ns, snapshot.retention_deadline_ns)
        )
    ):
        raise ValueError("native failure snapshot clock differs")
    operations = {item.operation_id: item for item in snapshot.operations}
    if len(operations) != len(snapshot.operations):
        raise ValueError("native failure snapshot duplicate operation")
    for item in snapshot.operations:
        if (
            int(item.started_ns) > int(snapshot.observed_ns)
            or (item.state == "pending") != (item.settled_ns is None)
            or (
                item.settled_ns is not None
                and not int(item.started_ns) <= int(item.settled_ns) <= int(snapshot.observed_ns)
            )
        ):
            raise ValueError("native failure snapshot operation clock differs")
    if len({item.operation_id for item in snapshot.handles}) != len(snapshot.handles) or any(
        item.operation_id not in operations for item in snapshot.handles
    ):
        raise ValueError("native failure snapshot handle ownership differs")
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    if len(artifacts) != len(snapshot.artifacts):
        raise ValueError("native failure snapshot duplicate artifact")
    if (
        sum(item.retained_bytes for item in snapshot.artifacts) > FAILURE_RAW_BYTES
        or sum(item.retained_bytes - item.acknowledged_bytes for item in snapshot.artifacts)
        != snapshot.held_bytes
        or sum(len(item.source_record_refs) for item in snapshot.artifacts) > FAILURE_CHUNKS
    ):
        raise ValueError("native failure snapshot cumulative inventory differs")
    if set(proof["artifacts"]) - set(artifacts):
        raise ValueError("native failure snapshot missing ACKed artifact")
    for item in snapshot.artifacts:
        if (
            item.retained_bytes > item.observed_bytes
            or item.acknowledged_bytes > item.retained_bytes
            or int(item.received_ns)
            > min(int(snapshot.observed_ns), int(snapshot.retention_deadline_ns))
            or any(
                ref.artifact_offset + ref.bytes > item.acknowledged_bytes
                for ref in item.source_record_refs
            )
        ):
            raise ValueError("native failure snapshot artifact bounds differ")
        prior = proof["artifacts"].get(item.artifact_id)
        if prior is None:
            if item.acknowledged_bytes or item.source_record_refs:
                raise ValueError("native failure snapshot unACKed artifact claimed")
            continue
        if (
            prior["metadata"]
            != (
                item.observed_bytes,
                item.retained_bytes,
                item.sha256,
                item.received_ns,
                snapshot.failure_ns,
                snapshot.retention_deadline_ns,
            )
            or prior["acknowledged_bytes"] != item.acknowledged_bytes
            or prior["source_record_refs"] != [ref.model_dump() for ref in item.source_record_refs]
        ):
            raise ValueError("native failure snapshot ACK inventory differs")
    expected_disposition = (
        "pending"
        if any(item.state == "pending" for item in snapshot.operations)
        else "partial"
        if snapshot.discarded_bytes or snapshot.held_bytes or snapshot.handles
        else "settled"
    )
    if snapshot.disposition != expected_disposition:
        raise ValueError("native failure snapshot disposition differs")
    return {
        "snapshot_sha256": _digest(snapshot_raw),
        "failure_open_digest": proof["failure_open_digest"],
        "failure_last_ack_digest": proof["failure_last_ack_digest"],
        "failure_ack_count": proof["failure_ack_count"],
        "artifact_count": len(snapshot.artifacts),
        "acknowledged_bytes": sum(item.acknowledged_bytes for item in snapshot.artifacts),
        "evidence_state": (
            "complete-evidence" if expected_disposition == "settled" else "partial-evidence"
        ),
    }
