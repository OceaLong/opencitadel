"""Pure-file v3 failed close tests; never a browser/native runtime acceptance."""

import base64
import hashlib
import json
import os

import pytest
from scripts.execution_capacity import attempt, native_failed_close, native_transport
from scripts.execution_capacity.attempt import AttemptLedger, ReadOnlyAttemptLedger, digest, encode
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_failed_close import (
    NativeEvidenceManifestV3,
    NativeFailureCloseRowV1,
    close_failed_host,
    reopen_failed_host_commitment,
)
from scripts.execution_capacity.native_failed_transport import prepare_failed_files
from scripts.execution_capacity.native_failure_transport import NativeFailureDrainWriter
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter
from scripts.execution_capacity.test_native_failed_transport import _callback, _setup, _snapshot


def _prepared(root, monkeypatch, *, content=b"", acknowledged=0, observations=(), source_ref=None):
    fixture, command, key, plan = _setup(monkeypatch)
    raw = _snapshot(fixture["command"], content=content, acknowledged=acknowledged)
    if source_ref is not None:
        row = json.loads(raw)
        row["artifacts"][0]["source_record_refs"] = [source_ref]
        raw = _canonical(row)
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for sequence, observation in enumerate(observations, 1):
            record = {
                **fixture["records"][0],
                "sequence": sequence,
                "received_ns": str(20 + sequence),
                "observation": observation,
            }
            native.append_record(_canonical(record) + b"\n")
        with NativeFailureDrainWriter.create(native, wire_version=2) as drain:
            if acknowledged:
                chunk = _callback(fixture["command"], content)
                if source_ref is not None:
                    chunk["source_record_ref"] = source_ref
                drain.append_chunk(chunk)
            prepared = prepare_failed_files(native, drain, raw)
            assert prepared.snapshot_sha256 == hashlib.sha256(raw).hexdigest()
    return key, plan, raw


def _manifest(root, key):
    return NativeEvidenceManifestV3.model_validate(
        json.loads((root / f"native-{key}" / "manifest.json").read_bytes())
    )


def _rechain(root, plan, change):
    path = root / "attempt.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    change(rows)
    previous = digest(plan)
    output = []
    for sequence, old in enumerate(rows, 1):
        row = {
            "sequence": sequence,
            "previous": previous,
            "kind": old["kind"],
            "body": old["body"],
        }
        previous = digest(row)
        output.append(encode({**row, "digest": previous}) + b"\n")
    path.write_bytes(b"".join(output))


def test_failed_close_zero_record_zero_callback_is_diagnostic_only(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, raw = _prepared(root, monkeypatch)
    commitment = close_failed_host(root, plan, key)
    manifest = _manifest(root, key)
    assert commitment.state == "failed"
    assert commitment.evidence_state == "complete-evidence"
    assert commitment.record_ack_count == 0
    assert commitment.failure_ack_count == 0
    assert manifest.shards == []
    assert manifest.record_artifacts == []
    assert manifest.failure.sha256 == hashlib.sha256(raw).hexdigest()
    assert manifest.failure_drain.size_bytes == 0
    assert reopen_failed_host_commitment(root, plan, key) == commitment
    with AttemptLedger.open(root, plan) as ledger:
        (close_row,) = ledger.records("native-failure-close")
        close = NativeFailureCloseRowV1.model_validate(close_row["body"])
        assert close.record_last_ack_digest is None
        assert close.failure_last_ack_digest == close.failure_open_digest
    with pytest.raises(ValueError, match="already closed"):
        close_failed_host(root, plan, key)
    with AttemptLedger.open(root, plan) as ledger:
        ledger.append("unrelated-host-note", {"command_sha256": "other"})
    assert reopen_failed_host_commitment(root, plan, key) == commitment


def test_failed_close_partial_snapshot_and_unterminated_record_inventory(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    observations = (
        {
            "kind": "private-chunk",
            "purpose": "rejected-capture",
            "artifact_id": "record-capture-id",
            "chunk_index": 0,
            "data": base64.b64encode(b"capture-prefix").decode(),
        },
        {
            "kind": "private-chunk",
            "purpose": "native-trace",
            "artifact_id": "native-trace",
            "chunk_index": 0,
            "data": base64.b64encode(b"trace-prefix").decode(),
        },
        {
            "kind": "image",
            "capture_id": "image-prefix-id",
            "chunk_index": 0,
            "data": base64.b64encode(b"image-prefix").decode(),
        },
    )
    content = b"a" * 49152 + b"unACKed-tail"
    key, plan, _raw = _prepared(
        root, monkeypatch, content=content, acknowledged=49152, observations=observations
    )
    commitment = close_failed_host(root, plan, key)
    manifest = _manifest(root, key)
    assert commitment.evidence_state == "partial-evidence"
    assert (commitment.record_ack_count, commitment.failure_ack_count) == (3, 1)
    assert [(row.purpose, row.state) for row in manifest.record_artifacts] == [
        ("rejected-capture", "prefix"),
        ("native-trace", "prefix"),
        ("image", "prefix"),
    ]
    assert manifest.shards[0].records == 3
    assert manifest.failure_drain.size_bytes > 0
    assert reopen_failed_host_commitment(root, plan, key) == commitment


def test_failed_close_full_ack_tombstone_is_complete_failed_evidence(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch, content=b"complete", acknowledged=8)
    commitment = close_failed_host(root, plan, key)
    assert (commitment.evidence_state, commitment.failure_ack_count) == ("complete-evidence", 1)
    assert _manifest(root, key).record_artifacts == []


def test_failed_close_snapshot_only_zero_ack_is_partial(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch, content=b"producer-held")
    commitment = close_failed_host(root, plan, key)
    assert commitment.evidence_state == "partial-evidence"
    assert (commitment.record_ack_count, commitment.failure_ack_count) == (0, 0)
    assert _manifest(root, key).shards == []
    assert _manifest(root, key).failure_drain.size_bytes == 0
    assert reopen_failed_host_commitment(root, plan, key) == commitment


def test_failed_close_explicit_record_source_can_have_distinct_callback_id(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    content = b"source"
    source_ref = {
        "kind": "native-record",
        "sequence": 1,
        "purpose": "rejected-capture",
        "artifact_id": "record-source-id",
        "chunk_index": 0,
        "artifact_offset": 0,
        "bytes": len(content),
    }
    observation = {
        "kind": "private-chunk",
        "purpose": "rejected-capture",
        "artifact_id": "record-source-id",
        "chunk_index": 0,
        "data": base64.b64encode(content).decode(),
    }
    key, plan, _raw = _prepared(
        root,
        monkeypatch,
        content=content,
        acknowledged=len(content),
        observations=(observation,),
        source_ref=source_ref,
    )
    commitment = close_failed_host(root, plan, key)
    assert commitment.evidence_state == "complete-evidence"
    assert _manifest(root, key).record_artifacts[0].artifact_id == "record-source-id"
    assert reopen_failed_host_commitment(root, plan, key) == commitment


@pytest.mark.parametrize(
    "defect", ["snapshot-offset", "drain-suffix", "extra", "symlink", "shard", "zero-shard"]
)
def test_failed_close_rejects_false_or_extra_precommit_source(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    if defect == "shard":
        observations = (
            {
                "kind": "private-chunk",
                "purpose": "rejected-capture",
                "artifact_id": "capture-source",
                "chunk_index": 0,
                "data": base64.b64encode(b"source").decode(),
            },
        )
        key, plan, _raw = _prepared(root, monkeypatch, observations=observations)
    else:
        key, plan, _raw = _prepared(root, monkeypatch)
    native = root / f"native-{key}"
    if defect == "snapshot-offset":
        row = json.loads((native / "failure.json").read_bytes())
        row["held_bytes"] = 1
        (native / "failure.json").write_bytes(_canonical(row))
    elif defect == "drain-suffix":
        with (native / "failure-drain.ndjson").open("ab") as stream:
            stream.write(b"X")
    elif defect == "extra":
        (native / "extra.bin").write_bytes(b"X")
    elif defect == "symlink":
        (native / "foreign-link").symlink_to(native / "command.json")
    elif defect == "zero-shard":
        (native / "records" / "000000.ndjson").write_bytes(b"")
    else:
        with (native / "records" / "000000.ndjson").open("ab") as stream:
            stream.write(b"X")
    with pytest.raises((ValueError, OSError)):
        close_failed_host(root, plan, key)
    assert not (native / "manifest.json").exists()
    with AttemptLedger.open(root, plan) as ledger:
        assert ledger.records("native-failure-close") == ()


def test_failed_close_rejects_mixed_success_row_even_with_valid_chain(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    with AttemptLedger.open(root, plan) as ledger:
        ledger.append("native-close", {"command_sha256": key})
    with pytest.raises(ValueError, match="successful native close mixed"):
        close_failed_host(root, plan, key)
    assert not (root / f"native-{key}" / "manifest.json").exists()


def test_failed_close_requires_release_of_original_attempt_lock(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    with AttemptLedger.open(root, plan), pytest.raises(BlockingIOError):
        close_failed_host(root, plan, key)
    assert not (root / f"native-{key}" / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["native-record-ack", "native-failure-open", "native-failure-ack"])
def test_failed_close_rejects_physically_expanded_canonical_ack_frame(tmp_path, monkeypatch, kind):
    root = tmp_path / "attempt"
    observation = {
        "kind": "private-chunk",
        "purpose": "rejected-capture",
        "artifact_id": "capture-source",
        "chunk_index": 0,
        "data": base64.b64encode(b"source").decode(),
    }
    key, plan, _raw = _prepared(
        root, monkeypatch, content=b"x", acknowledged=1, observations=(observation,)
    )
    path = root / "attempt.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    index = next(index for index, line in enumerate(lines) if json.loads(line)["kind"] == kind)
    lines[index] = lines[index][:-1] + b" " * 2000 + b"\n"
    assert len(lines[index]) > 1536
    path.write_bytes(b"".join(lines))
    with pytest.raises(ValueError, match="noncanonical attempt row"):
        close_failed_host(root, plan, key)
    with pytest.raises(ValueError, match="noncanonical original attempt row"):
        ReadOnlyAttemptLedger.open(root, origin=root, budget=EvidenceBudget())
    assert not (root / f"native-{key}" / "manifest.json").exists()


@pytest.mark.parametrize("defect", ["count", "digest", "duplicate", "late-ack"])
def test_fresh_failed_commitment_rejects_valid_chain_forged_close(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    close_failed_host(root, plan, key)
    if defect == "late-ack":
        with AttemptLedger.open(root, plan) as ledger:
            ledger.append("native-failure-ack", {"command_sha256": key})
    else:

        def change(rows):
            close = next(row for row in rows if row["kind"] == "native-failure-close")
            if defect == "count":
                close["body"]["record_ack_count"] = 1
            elif defect == "digest":
                close["body"]["failure_last_ack_digest"] = "0" * 64
            else:
                rows.append(dict(close))

        _rechain(root, plan, change)
    with pytest.raises((ValueError, OSError)):
        reopen_failed_host_commitment(root, plan, key)


def test_failed_close_rechecks_source_after_manifest_before_close_ack(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    native = root / f"native-{key}"
    original = native_failed_close._receipt

    def mutate_after_manifest(root_path, relative, limit, *, return_raw=False):
        result = original(root_path, relative, limit, return_raw=return_raw)
        if relative == "manifest.json":
            (native / "failure.json").write_bytes(b"X")
        return result

    monkeypatch.setattr(native_failed_close, "_receipt", mutate_after_manifest)
    with pytest.raises((ValueError, OSError)):
        close_failed_host(root, plan, key)
    with AttemptLedger.open(root, plan) as ledger:
        assert ledger.records("native-failure-close") == ()


@pytest.mark.parametrize("defect", ["short-write", "fsync", "append", "journal-short-write"])
def test_failed_close_write_fault_never_returns_commitment(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    native = root / f"native-{key}"
    if defect == "short-write":

        def short_write(fd, raw):
            os.write(fd, raw[:1])
            raise OSError("injected failed manifest short write")

        monkeypatch.setattr(native_transport, "_write_exact", short_write)
    elif defect == "fsync":
        original = native_failed_close._sync_directory

        def fail_directory(path):
            if path == native and (native / "manifest.json").exists():
                raise OSError("injected failed manifest directory fsync")
            return original(path)

        monkeypatch.setattr(native_failed_close, "_sync_directory", fail_directory)
    elif defect == "append":
        original = AttemptLedger.append

        def fail_close(self, kind, body):
            if kind == "native-failure-close":
                raise OSError("injected failed close ACK append")
            return original(self, kind, body)

        monkeypatch.setattr(AttemptLedger, "append", fail_close)
    else:
        original_append = AttemptLedger.append
        original_write = attempt.os.write
        in_close = False

        def short_journal_write(fd, raw):
            if in_close:
                return original_write(fd, raw[:1])
            return original_write(fd, raw)

        def append_close(self, kind, body):
            nonlocal in_close
            if kind == "native-failure-close":
                in_close = True
            return original_append(self, kind, body)

        monkeypatch.setattr(attempt.os, "write", short_journal_write)
        monkeypatch.setattr(AttemptLedger, "append", append_close)
    with pytest.raises(OSError, match=r"short attempt journal write|injected"):
        close_failed_host(root, plan, key)
    if defect == "journal-short-write":
        monkeypatch.setattr(attempt.os, "write", original_write)
    if defect in {"append", "journal-short-write"}:
        monkeypatch.setattr(
            AttemptLedger, "append", original if defect == "append" else original_append
        )
    if defect == "journal-short-write":
        with pytest.raises(ValueError, match="incomplete/oversize attempt row"):
            reopen_failed_host_commitment(root, plan, key)
        return
    with AttemptLedger.open(root, plan) as ledger:
        assert ledger.records("native-failure-close") == ()
    with pytest.raises(ValueError, match="unique failed native close absent"):
        reopen_failed_host_commitment(root, plan, key)


@pytest.mark.parametrize(
    "member", ["failure.json", "failure-drain.ndjson", "command.json", "manifest.json"]
)
def test_fresh_failed_commitment_detects_changed_member(tmp_path, monkeypatch, member):
    root = tmp_path / "attempt"
    key, plan, _raw = _prepared(root, monkeypatch)
    close_failed_host(root, plan, key)
    path = root / f"native-{key}" / member
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:] if raw else b"X")
    with pytest.raises((ValueError, OSError)):
        reopen_failed_host_commitment(root, plan, key)


def test_fresh_failed_commitment_detects_changed_record_shard(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    observation = {
        "kind": "private-chunk",
        "purpose": "rejected-capture",
        "artifact_id": "capture-source",
        "chunk_index": 0,
        "data": base64.b64encode(b"source").decode(),
    }
    key, plan, _raw = _prepared(root, monkeypatch, observations=(observation,))
    close_failed_host(root, plan, key)
    path = root / f"native-{key}" / "records" / "000000.ndjson"
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    with pytest.raises((ValueError, OSError)):
        reopen_failed_host_commitment(root, plan, key)
