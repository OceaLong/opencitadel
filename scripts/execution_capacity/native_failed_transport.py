"""Prepare exact producer failure bytes without creating a failed host commitment.

This closes both in-process write streams and leaves a diagnostic source prefix.
A separate fresh-ledger close must verify and commit any eventual v3 manifest.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import NativeFailureSnapshotV2
from scripts.execution_capacity.attempt import digest
from scripts.execution_capacity.native_failure_transport import (
    FAILURE_CHUNKS,
    FAILURE_FILE_BYTES,
    FailureAckRowV2,
    FailureOpenRowV2,
    NativeFailureDrainWriter,
    _verified_record_prefix,
)
from scripts.execution_capacity.native_raw import IDENTITY, LINE_BYTES, MANIFEST_BYTES, _canonical
from scripts.execution_capacity.native_transport import (
    NativeHostWriter,
    _digest,
    _new_file,
    _private_size,
    _sync_directory,
)
from scripts.execution_capacity.ownership import _open_private, _private_directory


@dataclass(frozen=True)
class PreparedFailedNative:
    """A local retained prefix only; never a manifest or consumer commitment."""

    state: Literal["prepared-prefix-only"]
    root: Path
    command_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    record_ack_count: int
    failure_ack_count: int


def _owned_open(native: NativeHostWriter, drain: NativeFailureDrainWriter):
    ledger = native.ledger
    if ledger.closed:
        raise ValueError("open owned native attempt ledger required")
    try:
        os.fstat(ledger.fd)
        os.fstat(ledger.lock)
    except OSError as error:
        raise ValueError("open owned native attempt ledger required") from error
    if (
        drain.ledger is not ledger
        or drain.root != native.root
        or drain.key != native.key
        or drain.clock_digest != native.clock_digest
        or drain.command != native.command
        or drain.wire_version != 2
        or drain.path != native.root / "failure-drain.ndjson"
        or drain.poisoned
        or drain.fd is None
        or native.poisoned
        or native.finished
        or ledger.poisoned
        or native.root != ledger.root / f"native-{native.key}"
    ):
        raise ValueError("same unpoisoned wire2 native failure owner required")
    clocks = ledger.records("host-clock")
    commands = [
        row
        for row in ledger.records("native-command")
        if row["body"].get("command_sha256") == native.key
    ]
    opens = [
        row
        for row in ledger.records("native-failure-open")
        if row["body"].get("command_sha256") == native.key
    ]
    if (
        len(clocks) != 1
        or len(commands) != 1
        or len(opens) != 1
        or any(
            row["body"].get("command_sha256") == native.key
            for kind in ("native-close", "native-failure-close")
            for row in ledger.records(kind)
        )
        or not clocks[0]["sequence"] < commands[0]["sequence"] < opens[0]["sequence"]
        or opens[0]["sequence"] != drain.open_sequence
        or digest(clocks[0]["body"]) != native.clock_digest
        or commands[0]["body"].get("host_clock_digest") != native.clock_digest
        or commands[0]["body"].get("root") != native.root.name
    ):
        raise ValueError("unique native failure clock/command/open required")
    opened = FailureOpenRowV2.model_validate(opens[0]["body"])
    if (
        opened.command_sha256 != native.key
        or opened.host_clock_digest != native.clock_digest
        or opened.file != drain.path.name
    ):
        raise ValueError("native failure open owner differs")
    return ledger, opens[0]


def _snapshot(raw: bytes, native: NativeHostWriter, drain: NativeFailureDrainWriter):
    if type(raw) is not bytes or not 0 < len(raw) <= MANIFEST_BYTES:
        raise ValueError("bounded exact producer failure snapshot bytes required")
    data = strict_json(raw)
    if _canonical(data) != raw:
        raise ValueError("noncanonical producer failure snapshot")
    snapshot = NativeFailureSnapshotV2.model_validate(data)
    if any(getattr(snapshot, name) != getattr(native.command, name) for name in IDENTITY):
        raise ValueError("producer failure snapshot foreign command")
    if (
        int(snapshot.retention_deadline_ns) != int(snapshot.failure_ns) + 10_000_000_000
        or int(snapshot.observed_ns) < int(snapshot.failure_ns)
        or (
            drain.failure_clock is not None
            and drain.failure_clock != (snapshot.failure_ns, snapshot.retention_deadline_ns)
        )
    ):
        raise ValueError("producer failure snapshot clock differs")
    return snapshot


def _check_owned_prefix(native: NativeHostWriter, drain: NativeFailureDrainWriter, opened):
    ledger = native.ledger
    with os.fdopen(_open_private(native.root / "command.json", os.O_RDONLY), "rb") as stream:
        command_raw = stream.read(LINE_BYTES)
    if (
        len(command_raw) != native.command_file.size_bytes
        or _digest(command_raw) != native.key
        or _canonical(strict_json(command_raw)) != command_raw
    ):
        raise ValueError("native failure command bytes changed")
    records = [
        row
        for row in ledger.records("native-record-ack")
        if row["body"].get("command_sha256") == native.key
    ]
    callbacks = [
        row
        for row in ledger.records("native-failure-ack")
        if row["body"].get("command_sha256") == native.key
    ]
    if len(records) != native.sequence or len(callbacks) != drain.count:
        raise ValueError("native failure in-memory ACK boundary differs")
    _verified_record_prefix(
        ledger,
        native.root,
        native.command,
        native.key,
        native.clock_digest,
        opened["sequence"],
        {},
    )
    if len(callbacks) > FAILURE_CHUNKS or drain.file_bytes > FAILURE_FILE_BYTES:
        raise ValueError("native failure callback spool bound exceeded")
    offset = 0
    with os.fdopen(_open_private(drain.path, os.O_RDONLY), "rb") as stream:
        for sequence, row in enumerate(callbacks, 1):
            ack = FailureAckRowV2.model_validate(row["body"])
            if (
                row["sequence"] <= opened["sequence"]
                or ack.command_sha256 != native.key
                or ack.host_clock_digest != native.clock_digest
                or ack.sequence != sequence
                or ack.offset != offset
            ):
                raise ValueError("native failure callback ACK owner/order differs")
            line = stream.read(ack.bytes)
            if len(line) != ack.bytes or not line.endswith(b"\n") or _digest(line) != ack.sha256:
                raise ValueError("native failure callback ACKed line changed")
            offset += ack.bytes
        if stream.read(1):
            raise ValueError("native failure unacknowledged drain suffix")
    if offset != drain.file_bytes or _private_size(drain.path) != offset:
        raise ValueError("native failure unacknowledged drain suffix")
    _private_directory(native.root)
    _private_directory(native.root / "records")
    expected = {"command.json", "records", "failure-drain.ndjson"}
    members = set()
    for path in native.root.iterdir():
        if path.is_symlink() or (path.name == "records") != path.is_dir():
            raise ValueError("native failure foreign member type")
        members.add(path.name)
    if members != expected:
        raise ValueError("native failure pre-snapshot fileset differs")
    return len(records), len(callbacks)


def prepare_failed_files(
    native: NativeHostWriter, drain: NativeFailureDrainWriter, producer_snapshot_raw: bytes
) -> PreparedFailedNative:
    """Seal owned streams and retain exact raw snapshot; caller releases ledger next."""
    if type(native) is not NativeHostWriter or type(drain) is not NativeFailureDrainWriter:
        raise TypeError("actual native and failure writers required")
    with native.ledger.thread_lock:
        _, opened = _owned_open(native, drain)
        native.finished = True  # blocks later appends under the same lifecycle lock
        try:
            _snapshot(producer_snapshot_raw, native, drain)
            native._seal_shard()
            os.fsync(drain.fd)
            drain.close()
            _sync_directory(native.root / "records")
            _sync_directory(native.root)
            record_count, failure_count = _check_owned_prefix(native, drain, opened)
            _new_file(native.root / "failure.json", producer_snapshot_raw)
            _sync_directory(native.root)
            return PreparedFailedNative(
                state="prepared-prefix-only",
                root=native.root,
                command_sha256=native.key,
                snapshot_sha256=hashlib.sha256(producer_snapshot_raw).hexdigest(),
                snapshot_bytes=len(producer_snapshot_raw),
                record_ack_count=record_count,
                failure_ack_count=failure_count,
            )
        except BaseException:
            native.poisoned = True
            drain.poisoned = True
            native.close()
            drain.close()
            raise
