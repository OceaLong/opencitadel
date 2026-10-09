"""Host-owned native record spool. A durable attempt ACK follows each exact line.

This is the record transport boundary, not a browser launcher or a capacity pass.
Failure-drain callbacks require their separate ledger before failed evidence can
be closed or promoted; this writer therefore only closes a complete record set.
"""

import base64
import binascii
import hashlib
import os
import re
import time
from pathlib import Path

from scripts.acceptance.capacity_completion import validate_native_trace_completion
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import NativeCommand, NativeRecord
from scripts.execution_capacity.attempt import AttemptLedger, digest
from scripts.execution_capacity.attempt import encode as attempt_encode
from scripts.execution_capacity.native_raw import (
    IDENTITY,
    LINE_BYTES,
    MAX_BINARY_ARTIFACTS,
    MAX_RECORDS,
    SHARD_BYTES,
    NativeArtifact,
    NativeEvidenceManifest,
    NativeFile,
    NativeHostCommitment,
    NativeShard,
    _canonical,
)
from scripts.execution_capacity.ownership import _open_private, _private_directory


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exact(descriptor, raw):
    if os.write(descriptor, raw) != len(raw):
        raise OSError("short native evidence write; partial bytes retained")
    os.fsync(descriptor)


def _new_file(path, raw):
    descriptor = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        _write_exact(descriptor, raw)
    finally:
        os.close(descriptor)
    _sync_directory(path.parent)
    return NativeFile(path=path.name, sha256=_digest(raw), size_bytes=len(raw))


def _private_size(path):
    descriptor = _open_private(path, os.O_RDONLY)
    try:
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)


def _require_registered_command(ledger: AttemptLedger, command_sha256: str):
    """Bind the typed preregistration to the exact durable attempt plan."""
    with ledger.thread_lock:
        _require_registered_command_at(ledger.plan, ledger.root, command_sha256)


def _require_registered_command_at(plan: dict, location: Path, command_sha256: str):
    """Pure plan/file command binding at an explicit physical location."""
    expected = attempt_encode(plan) + b"\n"
    with os.fdopen(_open_private(location / "plan.json", os.O_RDONLY), "rb") as stream:
        if stream.read(len(expected) + 1) != expected:
            raise ValueError("immutable host plan changed after open")
    commands = plan.get("native_commands")
    if (
        type(commands) is not list
        or len(commands) > MAX_RECORDS
        or any(
            type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in commands
        )
        or len(set(commands)) != len(commands)
    ):
        raise ValueError("exact immutable native command list required")
    if type(command_sha256) is not str or command_sha256 not in commands:
        raise ValueError("native command absent from immutable host plan")


class NativeHostWriter:
    """One command, one ACK sequence; no recovery of an uncertain write in place."""

    @classmethod
    def create(cls, ledger: AttemptLedger, command_raw: bytes):
        if type(ledger) is not AttemptLedger or type(command_raw) is not bytes:
            raise TypeError("actual attempt ledger and exact command bytes required")
        if not 0 < len(command_raw) < LINE_BYTES or b"\n" in command_raw:
            raise ValueError("native command byte bound differs")
        command_data = strict_json(command_raw)
        if _canonical(command_data) != command_raw:
            raise ValueError("native command noncanonical JSON")
        command = NativeCommand.model_validate(command_data)
        key = _digest(command_raw)
        _require_registered_command(ledger, key)
        if any(
            row["body"].get("command_sha256") == key for row in ledger.records("native-command")
        ):
            raise ValueError("native command already consumed")
        clock_digest = ledger.bind_clock()
        root = ledger.root / f"native-{key}"
        _private_directory(ledger.root)
        if root.absolute() != root.resolve():
            raise ValueError("native spool path changed")
        os.mkdir(root, mode=0o700)
        _sync_directory(ledger.root)
        os.mkdir(root / "records", mode=0o700)
        _sync_directory(root)
        result = object.__new__(cls)
        result.root, result.ledger, result.command = root, ledger, command
        result.key, result.clock_digest = key, clock_digest
        result.sequence, result.poisoned, result.finished = 0, False, False
        result._closed_record, result._last_outcome, result._last_ack_ns = False, None, None
        result.shards, result.shard = [], None
        result.fd = None
        try:
            result.command_file = _new_file(root / "command.json", command_raw)
            ledger.append(
                "native-command",
                {
                    "command_sha256": key,
                    "root": root.name,
                    "size_bytes": len(command_raw),
                    "host_clock_digest": clock_digest,
                },
            )
            return result
        except BaseException:
            result.poisoned = True
            result.close()
            raise

    def _open_shard(self):
        ordinal = len(self.shards)
        path = self.root / "records" / f"{ordinal:06}.ndjson"
        self.fd = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        _sync_directory(path.parent)
        self.shard = {
            "ordinal": ordinal,
            "path": f"records/{ordinal:06}.ndjson",
            "first_sequence": self.sequence + 1,
            "records": 0,
            "size_bytes": 0,
            "hash": hashlib.sha256(),
            "last_sequence": self.sequence,
        }

    def _seal_shard(self):
        if self.fd is None:
            return
        os.fsync(self.fd)
        os.close(self.fd)
        self.fd = None
        row = self.shard
        self.shards.append(
            NativeShard(
                path=row["path"],
                sha256=row["hash"].hexdigest(),
                size_bytes=row["size_bytes"],
                ordinal=row["ordinal"],
                first_sequence=row["first_sequence"],
                last_sequence=row["last_sequence"],
                records=row["records"],
            )
        )
        self.shard = None

    def append_record(self, line: bytes):
        """Return an ACK only after both raw bytes and independent host row fsync."""
        with self.ledger.thread_lock:
            return self._append_record_locked(line)

    def _append_record_locked(self, line: bytes):
        if self.poisoned or self.finished or self.ledger.poisoned or self.ledger.closed:
            raise ValueError("native host writer closed or uncertain")
        try:
            if (
                type(line) is not bytes
                or not 1 < len(line) < LINE_BYTES
                or not line.endswith(b"\n")
            ):
                raise ValueError("native record line bound differs")
            data = strict_json(line[:-1])
            if _canonical(data) != line[:-1]:
                raise ValueError("native record noncanonical JSON")
            record = NativeRecord.model_validate(data)
            if (
                record.sequence != self.sequence + 1
                or record.sequence > MAX_RECORDS
                or any(getattr(record, name) != getattr(self.command, name) for name in IDENTITY)
                or int(record.received_ns) > int(self.command.deadline_ns)
                or (self.sequence and self._closed_record)
            ):
                raise ValueError("native record owner/order/deadline differs")
            if (
                record.observation.kind == "closed"
                and record.observation.records != record.sequence
            ):
                raise ValueError("native terminal record count differs")
            if self.fd is None or self.shard["size_bytes"] + len(line) > SHARD_BYTES:
                self._seal_shard()
                self._open_shard()
            offset = self.shard["size_bytes"]
            _write_exact(self.fd, line)
            ack_ns = time.monotonic_ns()
            if (
                ack_ns < int(record.received_ns)
                or (self._last_ack_ns is not None and ack_ns < self._last_ack_ns)
                or ack_ns > int(self.command.deadline_ns)
            ):
                raise ValueError("native host ACK clock/deadline differs")
            self.ledger.append(
                "native-record-ack",
                {
                    "command_sha256": self.key,
                    "host_clock_digest": self.clock_digest,
                    "sequence": record.sequence,
                    "shard": self.shard["ordinal"],
                    "offset": offset,
                    "bytes": len(line),
                    "sha256": _digest(line),
                    "producer_received_ns": record.received_ns,
                    "host_ack_ns": ack_ns,
                },
            )
            self.shard["hash"].update(line)
            self.shard["size_bytes"] += len(line)
            self.shard["records"] += 1
            self.shard["last_sequence"] = record.sequence
            self.sequence = record.sequence
            self._closed_record = record.observation.kind == "closed"
            self._last_outcome = record.observation.outcome if self._closed_record else None
            self._last_ack_ns = ack_ns
            return record.sequence
        except BaseException:
            self.poisoned = True
            raise

    def _artifacts(self):
        states = {}
        for shard in self.shards:
            shard_hash, shard_size = hashlib.sha256(), 0
            with os.fdopen(_open_private(self.root / shard.path, os.O_RDONLY), "rb") as stream:
                for line in stream:
                    shard_hash.update(line)
                    shard_size += len(line)
                    if not 1 < len(line) < LINE_BYTES or not line.endswith(b"\n"):
                        raise ValueError("native artifact source line differs")
                    row = NativeRecord.model_validate(strict_json(line[:-1]))
                    observation = row.observation
                    if observation.kind not in {
                        "image",
                        "private-chunk",
                        "capture",
                        "trace-completion",
                    }:
                        continue
                    if observation.kind in {"image", "private-chunk"}:
                        purpose = "image" if observation.kind == "image" else observation.purpose
                        artifact_id = (
                            observation.capture_id
                            if purpose == "image"
                            else observation.artifact_id
                        )
                        key = (purpose, artifact_id)
                        if key not in states:
                            if len(states) >= MAX_BINARY_ARTIFACTS:
                                raise ValueError("native artifact count exceeds bound")
                            states[key] = {
                                "hash": hashlib.sha256(),
                                "size": 0,
                                "sequences": [],
                                "owner": None,
                            }
                        state = states[key]
                        if state["owner"] is not None or observation.chunk_index != len(
                            state["sequences"]
                        ):
                            raise ValueError("native artifact chunk order differs")
                        try:
                            chunk = base64.b64decode(observation.data, validate=True)
                        except binascii.Error as error:
                            raise ValueError("native artifact base64 differs") from error
                        if (
                            not 0 < len(chunk) <= 49152
                            or base64.b64encode(chunk).decode() != observation.data
                        ):
                            raise ValueError("native artifact chunk bytes differ")
                        state["hash"].update(chunk)
                        state["size"] += len(chunk)
                        state["sequences"].append(row.sequence)
                    else:
                        key = (
                            "image" if observation.kind == "capture" else "native-trace",
                            observation.capture_id
                            if observation.kind == "capture"
                            else observation.artifact_id,
                        )
                        state = states.get(key)
                        if state is None or state["owner"] is not None:
                            raise ValueError("native artifact terminal owner differs")
                        if observation.kind == "capture":
                            if (
                                state["size"] != observation.retained_bytes
                                or len(state["sequences"]) != observation.chunks
                                or state["hash"].hexdigest() != observation.retained_sha256
                            ):
                                raise ValueError("native capture artifact closure differs")
                        else:
                            validate_native_trace_completion(
                                row,
                                clock_id=self.command.clock_id,
                                deadline_ns=int(self.command.deadline_ns),
                                retained_bytes=state["size"],
                                retained_chunks=len(state["sequences"]),
                                retained_sha256=state["hash"].hexdigest(),
                            )
                        state["owner"] = row.sequence
            if shard_size != shard.size_bytes or shard_hash.hexdigest() != shard.sha256:
                raise ValueError("native artifact source shard changed")
        artifacts = []
        for ordinal, ((purpose, artifact_id), state) in enumerate(states.items()):
            if purpose == "rejected-capture" or state["owner"] is None:
                raise ValueError("failed or unclosed artifact in complete native spool")
            artifacts.append(
                NativeArtifact(
                    ordinal=ordinal,
                    purpose=purpose,
                    artifact_id=artifact_id,
                    sha256=state["hash"].hexdigest(),
                    size_bytes=state["size"],
                    chunk_sequences=state["sequences"],
                    owner_sequence=state["owner"],
                )
            )
        return artifacts

    def finish_complete(self):
        with self.ledger.thread_lock:
            return self._finish_complete_locked()

    def _finish_complete_locked(self):
        try:
            if (
                self.poisoned
                or self.finished
                or self.ledger.poisoned
                or self.ledger.closed
                or not self.sequence
                or not self._closed_record
            ):
                raise ValueError("complete native record closure absent")
            if any(
                row["body"].get("command_sha256") == self.key
                for kind in ("native-failure-open", "native-failure-ack")
                for row in self.ledger.records(kind)
            ) or os.path.lexists(self.root / "failure-drain.ndjson"):
                raise ValueError("native failure ownership forbids complete closure")
            if self._last_outcome != "observations-closed" or self._last_ack_ns > int(
                self.command.deadline_ns
            ):
                raise ValueError("native successful host ACK missed deadline")
            self._seal_shard()
            manifest = NativeEvidenceManifest(
                schema="opencitadel.native-evidence.v2",
                state="complete",
                command=self.command_file,
                shards=self.shards,
                artifacts=self._artifacts(),
                failure=None,
            )
            raw = _canonical(manifest.model_dump(by_alias=True))
            _new_file(self.root / "manifest.json", raw)
            _sync_directory(self.root / "records")
            _sync_directory(self.root)
            manifest_sha = _digest(raw)
            self.ledger.append(
                "native-close",
                {
                    "command_sha256": self.key,
                    "manifest_sha256": manifest_sha,
                    "last_ack_sequence": self.sequence,
                    "state": "complete",
                    "host_clock_digest": self.clock_digest,
                },
            )
            self.finished = True
            return manifest_sha
        except BaseException:
            self.poisoned = True
            raise

    def close(self):
        with self.ledger.thread_lock:
            self.finished = True
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def reopen_native_commitment(ledger_root: Path, plan: dict, command_sha256: str):
    """Derive the four consumer fields from a freshly verified host chain."""
    with AttemptLedger.open(ledger_root, plan) as ledger:
        _require_registered_command(ledger, command_sha256)
        clocks = ledger.records("host-clock")
        commands = [
            r
            for r in ledger.records("native-command")
            if r["body"].get("command_sha256") == command_sha256
        ]
        closes = [
            r
            for r in ledger.records("native-close")
            if r["body"].get("command_sha256") == command_sha256
        ]
        acks = [
            r
            for r in ledger.records("native-record-ack")
            if r["body"].get("command_sha256") == command_sha256
        ]
        if len(commands) != 1 or len(closes) != 1 or not acks:
            raise ValueError("unique closed native host attempt absent")
        command, close = commands[0]["body"], closes[0]["body"]
        if (
            len(clocks) != 1
            or clocks[0]["sequence"] >= commands[0]["sequence"]
            or digest(clocks[0]["body"]) != command["host_clock_digest"]
            or commands[0]["sequence"] >= acks[0]["sequence"]
            or acks[-1]["sequence"] >= closes[0]["sequence"]
            or command["root"] != f"native-{command_sha256}"
            or command["host_clock_digest"] != close["host_clock_digest"]
            or close["state"] != "complete"
            or close["last_ack_sequence"] != len(acks)
        ):
            raise ValueError("native host command/closure differs")
        root = ledger.root / command["root"]
        _private_directory(root)
        with os.fdopen(_open_private(root / "command.json", os.O_RDONLY), "rb") as stream:
            raw = stream.read(LINE_BYTES)
        if len(raw) != command["size_bytes"] or _digest(raw) != command_sha256:
            raise ValueError("native host command bytes changed")
        command_model = NativeCommand.model_validate(strict_json(raw))
        if _canonical(strict_json(raw)) != raw:
            raise ValueError("native host command noncanonical JSON")
        previous_shard, expected_offset, previous_host_ns = 0, 0, 0
        for sequence, ack_row in enumerate(acks, 1):
            ack = ack_row["body"]
            if (
                not commands[0]["sequence"] < ack_row["sequence"] < closes[0]["sequence"]
                or ack["sequence"] != sequence
                or ack["command_sha256"] != command_sha256
                or ack["host_clock_digest"] != command["host_clock_digest"]
                or (sequence == 1 and ack["shard"] != 0)
                or ack["shard"] not in {previous_shard, previous_shard + 1}
                or not 0 < ack["bytes"] < LINE_BYTES
                or ack["host_ack_ns"] < int(ack["producer_received_ns"])
                or ack["host_ack_ns"] < previous_host_ns
                or ack["host_ack_ns"] > int(command_model.deadline_ns)
            ):
                raise ValueError("native host ACK sequence/clock differs")
            if ack["shard"] != previous_shard:
                old_path = root / "records" / f"{previous_shard:06}.ndjson"
                if _private_size(old_path) != expected_offset:
                    raise ValueError("native host shard has unacknowledged suffix")
                previous_shard, expected_offset = ack["shard"], 0
            if ack["offset"] != expected_offset or expected_offset + ack["bytes"] > SHARD_BYTES:
                raise ValueError("native host ACK shard offset differs")
            path = root / "records" / f"{ack['shard']:06}.ndjson"
            with os.fdopen(_open_private(path, os.O_RDONLY), "rb") as stream:
                stream.seek(ack["offset"])
                line = stream.read(ack["bytes"])
            if len(line) != ack["bytes"] or _digest(line) != ack["sha256"]:
                raise ValueError("native host ACKed line changed")
            if not line.endswith(b"\n"):
                raise ValueError("native host ACKed line incomplete")
            record_data = strict_json(line[:-1])
            record = NativeRecord.model_validate(record_data)
            if (
                _canonical(record_data) != line[:-1]
                or record.sequence != sequence
                or any(getattr(record, name) != getattr(command_model, name) for name in IDENTITY)
                or record.received_ns != ack["producer_received_ns"]
            ):
                raise ValueError("native host ACKed record identity differs")
            expected_offset += ack["bytes"]
            previous_host_ns = ack["host_ack_ns"]
        last_path = root / "records" / f"{previous_shard:06}.ndjson"
        if _private_size(last_path) != expected_offset:
            raise ValueError("native host shard has unacknowledged suffix")
        with os.fdopen(_open_private(root / "manifest.json", os.O_RDONLY), "rb") as stream:
            manifest_raw = stream.read(8 * 1024 * 1024 + 1)
        if _digest(manifest_raw) != close["manifest_sha256"]:
            raise ValueError("native host manifest changed")
        return NativeHostCommitment(
            manifest_sha256=close["manifest_sha256"],
            command_sha256=command_sha256,
            last_ack_sequence=close["last_ack_sequence"],
            state="complete",
        )
