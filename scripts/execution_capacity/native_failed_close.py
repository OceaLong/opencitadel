"""Diagnostic v3 failed native close over one freshly locked host attempt.

This is not a native_raw reader, copied-source proof, or capacity acceptance.
The plan and entire attempt ledger still need a separate global size budget.
"""

import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator
from scripts.acceptance.capacity_records import Digest, Nat, Record
from scripts.execution_capacity.attempt import AttemptLedger, digest, encode
from scripts.execution_capacity.native_failed_records import (
    NativeFailedRecordArtifact,
    _derive_failed_record_inventory_on_ledger,
)
from scripts.execution_capacity.native_failure_transport import (
    FAILURE_CHUNKS,
    FAILURE_FILE_BYTES,
    _reconcile_failure_snapshot_v2_on_ledger,
)
from scripts.execution_capacity.native_raw import (
    LINE_BYTES,
    MANIFEST_BYTES,
    MAX_BINARY_ARTIFACTS,
    MAX_RECORDS,
    SHARD_BYTES,
    NativeFile,
    NativeShard,
    _canonical,
)
from scripts.execution_capacity.native_transport import (
    _digest,
    _new_file,
    _require_registered_command,
    _require_registered_command_at,
    _sync_directory,
)
from scripts.execution_capacity.ownership import _open_private, _private_directory

RECORD_ACK_ROW_BYTES = 1536
RECORD_ACK_LEDGER_BYTES = MAX_RECORDS * RECORD_ACK_ROW_BYTES


class NativeEvidenceManifestV3(Record):
    """Exact failed-only native subtree; zero ACKed record shards are legal."""

    schema_id: Literal["opencitadel.native-evidence.v3"] = Field(alias="schema")
    state: Literal["failed"]
    evidence_state: Literal["complete-evidence", "partial-evidence"]
    command: NativeFile
    shards: Annotated[list[NativeShard], Field(max_length=MAX_RECORDS)]
    record_artifacts: Annotated[
        list[NativeFailedRecordArtifact], Field(max_length=MAX_BINARY_ARTIFACTS)
    ]
    failure: NativeFile
    failure_drain: NativeFile

    @model_validator(mode="after")
    def exact_inventory(self):
        if (
            self.command.path != "command.json"
            or not 0 < self.command.size_bytes < LINE_BYTES
            or self.failure.path != "failure.json"
            or not 0 < self.failure.size_bytes <= MANIFEST_BYTES
            or self.failure_drain.path != "failure-drain.ndjson"
            or self.failure_drain.size_bytes > FAILURE_FILE_BYTES
        ):
            raise ValueError("failed native manifest member bound differs")
        sequence = 1
        for ordinal, shard in enumerate(self.shards):
            if shard.ordinal != ordinal or shard.first_sequence != sequence:
                raise ValueError("failed native shard sequence differs")
            sequence = shard.last_sequence + 1
        if sequence - 1 > MAX_RECORDS:
            raise ValueError("failed native record count differs")
        if any(item.ordinal != ordinal for ordinal, item in enumerate(self.record_artifacts)):
            raise ValueError("failed native record descriptor ordinal differs")
        paths = [
            self.command.path,
            self.failure.path,
            self.failure_drain.path,
            *(shard.path for shard in self.shards),
        ]
        if len(set(paths)) != len(paths):
            raise ValueError("failed native manifest duplicate member")
        return self


class NativeFailureCloseRowV1(Record):
    schema_id: Literal["opencitadel.native-failure-close.v1"] = Field(alias="schema")
    command_sha256: Digest
    host_clock_digest: Digest
    manifest_sha256: Digest
    failure_sha256: Digest
    record_ack_count: Annotated[Nat, Field(le=MAX_RECORDS)]
    record_last_ack_digest: Digest | None
    failure_open_digest: Digest
    failure_ack_count: Annotated[Nat, Field(le=FAILURE_CHUNKS)]
    failure_last_ack_digest: Digest
    failure_drain_sha256: Digest
    failure_drain_bytes: Annotated[Nat, Field(le=FAILURE_FILE_BYTES)]
    evidence_state: Literal["complete-evidence", "partial-evidence"]

    @model_validator(mode="after")
    def zero_ack_digests(self):
        if (self.record_ack_count == 0) != (self.record_last_ack_digest is None):
            raise ValueError("failed native zero record ACK digest differs")
        if self.failure_ack_count == 0 and (
            self.failure_last_ack_digest != self.failure_open_digest
        ):
            raise ValueError("failed native zero callback ACK digest differs")
        return self


class NativeFailedHostCommitment(Record):
    """Frozen diagnostic source receipt, never native_raw admission authority."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True, frozen=True)
    state: Literal["failed"]
    evidence_state: Literal["complete-evidence", "partial-evidence"]
    plan_sha256: Digest
    command_sha256: Digest
    manifest_sha256: Digest
    failure_sha256: Digest
    close_row_digest: Digest
    record_ack_count: Annotated[Nat, Field(le=MAX_RECORDS)]
    failure_ack_count: Annotated[Nat, Field(le=FAILURE_CHUNKS)]


def _identity(stat_result):
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _receipt(root: Path, relative: str, limit: int, *, return_raw=False):
    """Bounded private file receipt; materialize only the <=8 MiB snapshot."""
    path = root / relative
    hashed = hashlib.sha256()
    parts = [] if return_raw else None
    with os.fdopen(_open_private(path, os.O_RDONLY), "rb") as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > limit or (return_raw and before.st_size == 0):
            raise ValueError("failed native file size exceeds fixed bound")
        remaining = before.st_size
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise ValueError("failed native file truncated")
            hashed.update(chunk)
            if parts is not None:
                parts.append(chunk)
            remaining -= len(chunk)
        if stream.read(1) or _identity(before) != _identity(os.fstat(stream.fileno())):
            raise ValueError("failed native file changed during receipt")
        if _identity(before) != _identity(os.stat(path, follow_symlinks=False)):
            raise ValueError("failed native file path changed during receipt")
    receipt = NativeFile(path=relative, sha256=hashed.hexdigest(), size_bytes=before.st_size)
    return (receipt, b"".join(parts)) if parts is not None else receipt


def _exact_fileset(root: Path, shard_paths: set[str], *, with_manifest: bool):
    _private_directory(root)
    record_root = root / "records"
    _private_directory(record_root)
    expected_root = {"command.json", "records", "failure.json", "failure-drain.ndjson"}
    if with_manifest:
        expected_root.add("manifest.json")
    actual_root = set()
    for path in root.iterdir():
        if path.is_symlink() or (path.name == "records") != path.is_dir():
            raise ValueError("failed native foreign root member type")
        actual_root.add(path.name)
    if actual_root != expected_root:
        raise ValueError("failed native exact root fileset differs")
    actual_shards = set()
    for path in record_root.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("failed native foreign record member type")
        actual_shards.add(f"records/{path.name}")
    if actual_shards != shard_paths:
        raise ValueError("failed native exact record fileset differs")


def _key_rows(ledger: AttemptLedger, command_sha256: str, kind: str):
    return [
        row for row in ledger.records(kind) if row["body"].get("command_sha256") == command_sha256
    ]


def _candidate(
    ledger: AttemptLedger,
    command_sha256: str,
    *,
    physical_location: Path | None = None,
    fixture_limits=None,
):
    """Re-derive the complete physical failed candidate under one fresh lock."""
    if physical_location is None:
        _require_registered_command(ledger, command_sha256)
    else:
        _require_registered_command_at(ledger.plan, physical_location, command_sha256)
    if _key_rows(ledger, command_sha256, "native-close"):
        raise ValueError("successful native close mixed with failed candidate")
    commands = _key_rows(ledger, command_sha256, "native-command")
    opens = _key_rows(ledger, command_sha256, "native-failure-open")
    if len(commands) != 1 or len(opens) != 1:
        raise ValueError("unique failed native command/open absent")
    command = commands[0]["body"]
    if command.get("root") != f"native-{command_sha256}":
        raise ValueError("failed native command root differs")
    root = (ledger.root if physical_location is None else physical_location) / command["root"]
    _private_directory(root)
    snapshot_file, snapshot_raw = _receipt(
        root,
        "failure.json",
        MANIFEST_BYTES if fixture_limits is None else fixture_limits["failure"],
        return_raw=True,
    )
    reconciliation = _reconcile_failure_snapshot_v2_on_ledger(
        ledger,
        command_sha256,
        snapshot_raw,
        physical_location=physical_location,
        fixture_limits=fixture_limits,
    )
    artifacts = _derive_failed_record_inventory_on_ledger(
        ledger,
        command_sha256,
        physical_location=physical_location,
        fixture_limits=fixture_limits,
    )
    record_acks = _key_rows(ledger, command_sha256, "native-record-ack")
    failure_acks = _key_rows(ledger, command_sha256, "native-failure-ack")
    if len(record_acks) > MAX_RECORDS or len(failure_acks) > FAILURE_CHUNKS:
        raise ValueError("failed native ACK count exceeds fixed bound")
    record_ledger_bytes = 0
    for row in record_acks:
        size = len(encode({**row, "digest": digest(row)})) + 1
        record_ledger_bytes += size
        if size > RECORD_ACK_ROW_BYTES or record_ledger_bytes > RECORD_ACK_LEDGER_BYTES:
            raise ValueError("failed native record ACK ledger bound exceeded")
    shards = []
    for row in record_acks:
        ack = row["body"]
        if ack["shard"] == len(shards):
            shards.append(
                {
                    "ordinal": ack["shard"],
                    "first_sequence": ack["sequence"],
                    "last_sequence": ack["sequence"],
                    "records": 0,
                    "size_bytes": 0,
                }
            )
        shard = shards[ack["shard"]]
        shard["last_sequence"] = ack["sequence"]
        shard["records"] += 1
        shard["size_bytes"] = ack["offset"] + ack["bytes"]
    shard_models = []
    for row in shards:
        relative = f"records/{row['ordinal']:06}.ndjson"
        receipt = _receipt(
            root, relative, SHARD_BYTES if fixture_limits is None else fixture_limits["shard"]
        )
        if receipt.size_bytes != row["size_bytes"]:
            raise ValueError("failed native record shard receipt differs")
        shard_models.append(
            NativeShard(
                **receipt.model_dump(), **{k: v for k, v in row.items() if k != "size_bytes"}
            )
        )
    command_file = _receipt(
        root, "command.json", LINE_BYTES if fixture_limits is None else fixture_limits["command"]
    )
    if command_file.sha256 != command_sha256:
        raise ValueError("failed native command receipt differs")
    failure_drain = _receipt(
        root,
        "failure-drain.ndjson",
        FAILURE_FILE_BYTES if fixture_limits is None else fixture_limits["drain"],
    )
    expected_drain_bytes = (
        failure_acks[-1]["body"]["offset"] + failure_acks[-1]["body"]["bytes"]
        if failure_acks
        else 0
    )
    if failure_drain.size_bytes != expected_drain_bytes:
        raise ValueError("failed native drain receipt differs")
    manifest = NativeEvidenceManifestV3(
        schema="opencitadel.native-evidence.v3",
        state="failed",
        evidence_state=reconciliation["evidence_state"],
        command=command_file,
        shards=shard_models,
        record_artifacts=list(artifacts),
        failure=snapshot_file,
        failure_drain=failure_drain,
    )
    raw = _canonical(manifest.model_dump(by_alias=True))
    if len(raw) > MANIFEST_BYTES or (
        fixture_limits is not None and len(raw) > fixture_limits["manifest"]
    ):
        raise ValueError("failed native manifest exceeds fixed bound")
    close = NativeFailureCloseRowV1(
        schema="opencitadel.native-failure-close.v1",
        command_sha256=command_sha256,
        host_clock_digest=command["host_clock_digest"],
        manifest_sha256=_digest(raw),
        failure_sha256=snapshot_file.sha256,
        record_ack_count=len(record_acks),
        record_last_ack_digest=digest(record_acks[-1]) if record_acks else None,
        failure_open_digest=reconciliation["failure_open_digest"],
        failure_ack_count=len(failure_acks),
        failure_last_ack_digest=reconciliation["failure_last_ack_digest"],
        failure_drain_sha256=failure_drain.sha256,
        failure_drain_bytes=failure_drain.size_bytes,
        evidence_state=reconciliation["evidence_state"],
    )
    return root, manifest, raw, close


def _verify_failed_close_on_ledger(
    ledger: AttemptLedger,
    command_sha256: str,
    *,
    physical_location: Path | None = None,
    fixture_limits=None,
):
    closes = _key_rows(ledger, command_sha256, "native-failure-close")
    if len(closes) != 1:
        raise ValueError("unique failed native close absent")
    close_row = closes[0]
    close_model = NativeFailureCloseRowV1.model_validate(close_row["body"])
    related = (
        "native-command",
        "native-record-ack",
        "native-failure-open",
        "native-failure-ack",
        "native-close",
    )
    if any(
        row["sequence"] >= close_row["sequence"]
        for kind in related
        for row in _key_rows(ledger, command_sha256, kind)
    ):
        raise ValueError("native host writes after failed close")
    root, manifest, raw, expected = _candidate(
        ledger, command_sha256, physical_location=physical_location, fixture_limits=fixture_limits
    )
    if fixture_limits is not None and len(manifest.shards) > fixture_limits["shards"]:
        raise ValueError("failed diagnostic shard count exceeds fixture bound")
    _exact_fileset(root, {shard.path for shard in manifest.shards}, with_manifest=True)
    manifest_file, manifest_raw = _receipt(
        root,
        "manifest.json",
        MANIFEST_BYTES if fixture_limits is None else fixture_limits["manifest"],
        return_raw=True,
    )
    if (
        manifest_raw != raw
        or manifest_file.sha256 != close_model.manifest_sha256
        or close_model != expected
    ):
        raise ValueError("failed native manifest/close receipt differs")
    return NativeFailedHostCommitment(
        state="failed",
        evidence_state=expected.evidence_state,
        plan_sha256=_digest(encode(ledger.plan) + b"\n"),
        command_sha256=command_sha256,
        manifest_sha256=expected.manifest_sha256,
        failure_sha256=expected.failure_sha256,
        close_row_digest=digest(close_row),
        record_ack_count=expected.record_ack_count,
        failure_ack_count=expected.failure_ack_count,
    )


def reopen_failed_host_commitment(ledger_root: Path, plan: dict, command_sha256: str):
    """Freshly verify a failed close; return only a diagnostic host receipt."""
    with AttemptLedger.open(ledger_root, plan) as ledger:
        return _verify_failed_close_on_ledger(ledger, command_sha256)


def close_failed_host(ledger_root: Path, plan: dict, command_sha256: str):
    """Commit v3 failed evidence only after complete physical/source revalidation."""
    with AttemptLedger.open(ledger_root, plan) as ledger:
        if _key_rows(ledger, command_sha256, "native-failure-close"):
            raise ValueError("failed native command already closed")
        root, manifest, raw, close = _candidate(ledger, command_sha256)
        _exact_fileset(root, {shard.path for shard in manifest.shards}, with_manifest=False)
        close_body = close.model_dump(by_alias=True)
        proposed_row = {
            "sequence": len(ledger.rows) + 1,
            "previous": ledger.chain,
            "kind": "native-failure-close",
            "body": close_body,
        }
        if len(encode({**proposed_row, "digest": digest(proposed_row)})) + 1 > 1536:
            raise ValueError("failed native close ledger row bound exceeded")
        _new_file(root / "manifest.json", raw)
        _sync_directory(root / "records")
        _sync_directory(root)
        _exact_fileset(root, {shard.path for shard in manifest.shards}, with_manifest=True)
        manifest_file, manifest_raw = _receipt(
            root, "manifest.json", MANIFEST_BYTES, return_raw=True
        )
        if manifest_raw != raw or manifest_file.sha256 != close.manifest_sha256:
            raise ValueError("failed native just-written manifest differs")
        post_root, post_manifest, post_raw, post_close = _candidate(ledger, command_sha256)
        if post_root != root or post_manifest != manifest or post_raw != raw or post_close != close:
            raise ValueError("failed native source changed before close append")
        _exact_fileset(root, {shard.path for shard in manifest.shards}, with_manifest=True)
        ledger.append("native-failure-close", close_body)
    return reopen_failed_host_commitment(ledger_root, plan, command_sha256)
