"""Pure failed-path record inventory from the exact host-ACKed NativeRecord prefix.

These descriptors are diagnostic source facts. They do not write a v3 manifest,
append a failure close, or turn a failed prefix into successful source evidence.
"""

import base64
import binascii
import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from scripts.acceptance.capacity_completion import validate_native_trace_completion
from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import NativeCommand
from scripts.acceptance.capacity_records import ID, Digest, Nat, Pos, Record
from scripts.execution_capacity.attempt import AttemptLedger, digest
from scripts.execution_capacity.native_failure_transport import (
    FailureOpenRowV2,
    _verified_record_prefix,
)
from scripts.execution_capacity.native_raw import (
    LINE_BYTES,
    MAX_BINARY_ARTIFACTS,
    MAX_RECORDS,
    _canonical,
)
from scripts.execution_capacity.native_transport import (
    _digest,
    _require_registered_command,
    _require_registered_command_at,
)
from scripts.execution_capacity.ownership import _open_private, _private_directory

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class NativeFailedRecordArtifact(Record):
    """One exhaustive chunk-bearing artifact, or a zero-byte terminal trace."""

    ordinal: Annotated[Nat, Field(lt=MAX_BINARY_ARTIFACTS)]
    purpose: Literal["image", "native-trace", "rejected-capture"]
    artifact_id: ID
    state: Literal["prefix", "closed"]
    sha256: Digest
    size_bytes: Nat
    chunk_sequences: Annotated[
        list[Annotated[Pos, Field(le=MAX_RECORDS)]], Field(max_length=MAX_RECORDS)
    ]
    owner_sequence: Annotated[Pos, Field(le=MAX_RECORDS)] | None

    @model_validator(mode="after")
    def exact_closure(self):
        if (self.state == "closed") != (self.owner_sequence is not None):
            raise ValueError("failed record terminal state differs")
        if self.purpose == "rejected-capture" and self.state != "prefix":
            raise ValueError("rejected capture has no native terminal owner")
        if self.purpose == "native-trace" and self.artifact_id != "native-trace":
            raise ValueError("native trace artifact identity differs")
        if self.size_bytes == 0:
            if (
                self.purpose != "native-trace"
                or self.state != "closed"
                or self.chunk_sequences
                or self.sha256 != _EMPTY_SHA256
            ):
                raise ValueError("zero-byte failed record artifact differs")
        elif not self.chunk_sequences:
            raise ValueError("failed record artifact chunk sequence absent")
        if self.purpose == "image" and self.size_bytes > 8 * 1024 * 1024:
            raise ValueError("failed image exceeds retained limit")
        if self.purpose == "native-trace" and self.size_bytes > 32 * 1024 * 1024:
            raise ValueError("failed trace exceeds retained limit")
        if self.purpose == "rejected-capture" and self.size_bytes > 128 * 1024 * 1024:
            raise ValueError("rejected capture exceeds retained limit")
        if (
            self.purpose in {"image", "rejected-capture"}
            and len(self.chunk_sequences) != (self.size_bytes + 49151) // 49152
        ):
            raise ValueError("failed image chunk count differs")
        if any(
            left >= right
            for left, right in zip(self.chunk_sequences, self.chunk_sequences[1:], strict=False)
        ):
            raise ValueError("failed record artifact chunk order differs")
        if (
            self.owner_sequence is not None
            and self.chunk_sequences
            and self.owner_sequence <= self.chunk_sequences[-1]
        ):
            raise ValueError("failed record terminal precedes chunks")
        return self


class _Inventory:
    def __init__(self, command: NativeCommand):
        self.command = command
        self.states = {}
        self.status_seen = False

    def _state(self, purpose, artifact_id):
        key = (purpose, artifact_id)
        state = self.states.get(key)
        if state is None:
            if len(self.states) >= MAX_BINARY_ARTIFACTS:
                raise ValueError("failed record artifact count exceeds bound")
            state = {
                "hash": hashlib.sha256(),
                "size": 0,
                "sequences": [],
                "owner": None,
                "short_chunk": False,
            }
            self.states[key] = state
        return state

    def visit(self, record):
        if self.status_seen:
            raise ValueError("record after failed terminal status")
        observation = record.observation
        if observation.kind == "closed":
            if observation.outcome != "failed" or observation.records != record.sequence:
                raise ValueError("successful or false terminal in failed record prefix")
            self.status_seen = True
            return
        if observation.kind in {"image", "private-chunk"}:
            purpose = "image" if observation.kind == "image" else observation.purpose
            artifact_id = (
                observation.capture_id if observation.kind == "image" else observation.artifact_id
            )
            state = self._state(purpose, artifact_id)
            if (
                state["owner"] is not None
                or observation.chunk_index != len(state["sequences"])
                or state["short_chunk"]
            ):
                raise ValueError("failed record chunk owner/order differs")
            try:
                raw = base64.b64decode(observation.data, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("failed record chunk base64 differs") from error
            if not 0 < len(raw) <= 49152 or base64.b64encode(raw).decode() != observation.data:
                raise ValueError("failed record chunk bytes differ")
            limit = {
                "image": 8 * 1024 * 1024,
                "native-trace": 32 * 1024 * 1024,
                "rejected-capture": 128 * 1024 * 1024,
            }[purpose]
            if state["size"] + len(raw) > limit:
                raise ValueError("failed record artifact byte bound differs")
            state["hash"].update(raw)
            state["size"] += len(raw)
            state["sequences"].append(record.sequence)
            if purpose in {"image", "rejected-capture"} and len(raw) < 49152:
                state["short_chunk"] = True
            return
        if observation.kind == "capture":
            state = self.states.get(("image", observation.capture_id))
            if state is None or state["owner"] is not None:
                raise ValueError("failed image terminal has no open artifact")
            if (
                observation.retained_bytes != state["size"]
                or observation.chunks != len(state["sequences"])
                or observation.retained_sha256 != state["hash"].hexdigest()
                or int(observation.postcheck_ns) > int(record.received_ns)
            ):
                raise ValueError("failed image terminal bytes/clock differ")
            state["owner"] = record.sequence
            return
        if observation.kind == "trace-completion":
            state = self.states.get(("native-trace", observation.artifact_id))
            if state is None:
                if (
                    observation.retained_bytes
                    or observation.retained_chunks
                    or observation.retained_sha256 != _EMPTY_SHA256
                ):
                    raise ValueError("failed trace terminal has no ACKed chunks")
                state = self._state("native-trace", observation.artifact_id)
            if state["owner"] is not None:
                raise ValueError("duplicate failed trace terminal")
            if (
                observation.retained_bytes != state["size"]
                or observation.retained_chunks != len(state["sequences"])
                or observation.retained_sha256 != state["hash"].hexdigest()
                or any(
                    stamp is not None and int(stamp) > int(record.received_ns)
                    for stamp in (
                        observation.end_dispatched_ns,
                        observation.end_received_ns,
                        observation.complete_received_ns,
                        observation.eof_received_ns,
                    )
                )
            ):
                raise ValueError("failed trace terminal bytes/clock differ")
            if observation.parser == "complete":
                validate_native_trace_completion(
                    record,
                    clock_id=self.command.clock_id,
                    deadline_ns=int(self.command.deadline_ns),
                    retained_bytes=state["size"],
                    retained_chunks=len(state["sequences"]),
                    retained_sha256=state["hash"].hexdigest(),
                )
            state["owner"] = record.sequence

    def descriptors(self):
        return tuple(
            NativeFailedRecordArtifact(
                ordinal=ordinal,
                purpose=purpose,
                artifact_id=artifact_id,
                state="closed" if state["owner"] is not None else "prefix",
                sha256=state["hash"].hexdigest(),
                size_bytes=state["size"],
                chunk_sequences=state["sequences"],
                owner_sequence=state["owner"],
            )
            for ordinal, ((purpose, artifact_id), state) in enumerate(self.states.items())
        )


def derive_failed_record_inventory(ledger_root: Path, plan: dict, command_sha256: str):
    """Fresh-ledger, one-line-at-a-time derivation; no failed-close authority."""
    with AttemptLedger.open(ledger_root, plan) as ledger:
        return _derive_failed_record_inventory_on_ledger(ledger, command_sha256)


def _derive_failed_record_inventory_on_ledger(
    ledger: AttemptLedger,
    command_sha256: str,
    *,
    physical_location: Path | None = None,
    fixture_limits=None,
):
    """Borrow one already locked attempt for an atomic failed-close verification."""
    if physical_location is None:
        _require_registered_command(ledger, command_sha256)
    else:
        _require_registered_command_at(ledger.plan, physical_location, command_sha256)
    clocks = ledger.records("host-clock")
    commands = [
        row
        for row in ledger.records("native-command")
        if row["body"].get("command_sha256") == command_sha256
    ]
    opens = [
        row
        for row in ledger.records("native-failure-open")
        if row["body"].get("command_sha256") == command_sha256
    ]
    closes = [
        row
        for row in ledger.records("native-close")
        if row["body"].get("command_sha256") == command_sha256
    ]
    if len(clocks) != 1 or len(commands) != 1 or len(opens) != 1 or closes:
        raise ValueError("unique failed native record owner absent")
    command_row, open_row = commands[0], opens[0]
    command = command_row["body"]
    opened = FailureOpenRowV2.model_validate(open_row["body"])
    if (
        not clocks[0]["sequence"] < command_row["sequence"] < open_row["sequence"]
        or digest(clocks[0]["body"]) != command["host_clock_digest"]
        or command["root"] != f"native-{command_sha256}"
        or opened.command_sha256 != command_sha256
        or opened.host_clock_digest != command["host_clock_digest"]
    ):
        raise ValueError("failed native record clock/command/open differs")
    if any(
        row["sequence"] <= command_row["sequence"]
        for row in ledger.records("native-record-ack")
        if row["body"].get("command_sha256") == command_sha256
    ):
        raise ValueError("failed native record ACK precedes command")
    root = (ledger.root if physical_location is None else physical_location) / command["root"]
    _private_directory(root)
    with os.fdopen(_open_private(root / "command.json", os.O_RDONLY), "rb") as stream:
        raw = stream.read(LINE_BYTES)
    if (
        len(raw) != command["size_bytes"]
        or _digest(raw) != command_sha256
        or _canonical(strict_json(raw)) != raw
    ):
        raise ValueError("failed native record command bytes differ")
    command_model = NativeCommand.model_validate(strict_json(raw))
    inventory = _Inventory(command_model)
    _verified_record_prefix(
        ledger,
        root,
        command_model,
        command_sha256,
        command["host_clock_digest"],
        open_row["sequence"],
        {},
        on_record=inventory.visit,
        fixture_limits=fixture_limits,
    )
    return inventory.descriptors()


def verify_failed_record_inventory(
    ledger_root: Path, plan: dict, command_sha256: str, descriptors: list[dict]
):
    """Reject any omitted, reordered, duplicated, or forged descriptor."""
    if type(descriptors) is not list or len(descriptors) > MAX_BINARY_ARTIFACTS:
        raise ValueError("bounded failed record descriptor list required")
    claimed = tuple(NativeFailedRecordArtifact.model_validate(row) for row in descriptors)
    expected = derive_failed_record_inventory(ledger_root, plan, command_sha256)
    if claimed != expected:
        raise ValueError("failed record inventory differs from durable prefix")
    return expected
