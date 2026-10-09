"""Pure file/clock tests of native host bytes and independent durable ACKs."""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts.execution_capacity import attempt, native_transport
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_raw import NativeEvidenceSession, _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter, reopen_native_commitment

FIXTURE = Path("e2e/performance/native-wire-fixture-v1.json")


def _input(monkeypatch):
    monkeypatch.setattr(
        attempt,
        "host_clock",
        lambda: {
            "boot_id": "00000000-0000-0000-0000-000000000001",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )
    monkeypatch.setattr(native_transport.time, "monotonic_ns", lambda: 50)
    fixture = json.loads(FIXTURE.read_bytes())
    command = _canonical(fixture["command"])
    lines = [_canonical(row) + b"\n" for row in fixture["records"]]
    key = hashlib.sha256(command).hexdigest()
    return command, lines, key, {"native_commands": [key]}


def _open_native(root, plan, key):
    host = reopen_native_commitment(root, plan, key)
    return NativeEvidenceSession.open(
        root / f"native-{key}",
        host=host,
        budget=EvidenceBudget(bytes_limit=8 * 1024 * 1024, rows_limit=4096),
        index_bytes=256 * 1024,
    )


def test_exact_native_bytes_require_independent_host_ack_and_fresh_chain(tmp_path, monkeypatch):
    command, lines, key, plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    with AttemptLedger.create(root, plan) as ledger:
        with NativeHostWriter.create(ledger, command) as writer:
            for sequence, line in enumerate(lines, 1):
                assert writer.append_record(line) == sequence
                ack = ledger.records("native-record-ack")[-1]["body"]
                assert ack["sha256"] == hashlib.sha256(line).hexdigest()
                assert ack["host_ack_ns"] == 50
                assert ack["producer_received_ns"] in ("12", "13")
            writer.finish_complete()
        assert len(ledger.records("native-close")) == 1
    with _open_native(root, plan, key) as native:
        assert [record.sequence for record in native.records()] == [1, 2]
        assert native.structural_complete is True
        assert native.full_source_ready is False
        assert (root / f"native-{key}" / "records/000000.ndjson").read_bytes() == b"".join(lines)
        assert not list((root / f"native-{key}").rglob("*.bin"))


@pytest.mark.parametrize("defect", ["short-write", "fsync", "host-ack"])
def test_failed_write_or_host_ack_keeps_unacknowledged_prefix(tmp_path, monkeypatch, defect):
    command, lines, key, plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        if defect == "host-ack":
            original = ledger.append

            def fail_ack(kind, body):
                if kind == "native-record-ack":
                    raise OSError("injected host ledger failure")
                return original(kind, body)

            monkeypatch.setattr(ledger, "append", fail_ack)
        else:

            def fail_write(descriptor, raw):
                os.write(descriptor, raw[:5] if defect == "short-write" else raw)
                raise OSError("injected native file failure")

            monkeypatch.setattr(native_transport, "_write_exact", fail_write)
        with pytest.raises(OSError, match="injected"):
            writer.append_record(lines[0])
        with pytest.raises(ValueError, match="uncertain"):
            writer.append_record(lines[0])
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-close") == ()
    raw = (root / f"native-{key}" / "records/000000.ndjson").read_bytes()
    assert raw == (lines[0] if defect == "host-ack" or defect == "fsync" else lines[0][:5])
    with pytest.raises(ValueError, match="closed native host attempt absent"):
        reopen_native_commitment(root, plan, key)


def test_native_host_reopen_rejects_changed_raw_bytes_and_plan(tmp_path, monkeypatch):
    command, lines, key, plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for line in lines:
            writer.append_record(line)
        writer.finish_complete()
    with pytest.raises(ValueError, match="immutable attempt plan differs"):
        reopen_native_commitment(root, {"native_commands": []}, key)
    path = root / f"native-{key}" / "records/000000.ndjson"
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    with pytest.raises(ValueError, match="ACKed line changed"):
        reopen_native_commitment(root, plan, key)


def test_native_host_rejects_foreign_or_noncanonical_record_before_ack(tmp_path, monkeypatch):
    command, lines, _key, plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        foreign = json.loads(lines[0])
        foreign["sample_id"] = "another"
        with pytest.raises(ValueError, match="owner/order"):
            writer.append_record(_canonical(foreign) + b"\n")
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-close") == ()


def _trace_lines():
    fixture = json.loads(FIXTURE.read_bytes())
    first = fixture["records"][0]
    raw = b'{"traceEvents":[]}'
    chunk = {
        **first,
        "sequence": 2,
        "received_ns": "20",
        "observation": {
            "kind": "private-chunk",
            "artifact_id": "native-trace",
            "purpose": "native-trace",
            "chunk_index": 0,
            "data": base64.b64encode(raw).decode(),
        },
    }
    completion = {
        **first,
        "sequence": 3,
        "received_ns": "25",
        "observation": {
            "kind": "trace-completion",
            "artifact_id": "native-trace",
            "stream_id": "stream",
            "end_dispatched_ns": "21",
            "end_received_ns": "22",
            "complete_received_ns": "23",
            "eof_received_ns": "24",
            "data_loss": False,
            "parser": "complete",
            "observed_bytes": len(raw),
            "retained_bytes": len(raw),
            "retained_chunks": 1,
            "retained_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    closed = {**fixture["records"][-1], "sequence": 4, "received_ns": "26"}
    closed["observation"] = {**closed["observation"], "records": 4}
    return [_canonical(row) + b"\n" for row in (first, chunk, completion, closed)]


def test_native_trace_descriptor_uses_only_acknowledged_ndjson_bytes(tmp_path, monkeypatch):
    command, _lines, key, plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for line in _trace_lines():
            writer.append_record(line)
        writer.finish_complete()
    with _open_native(root, plan, key) as native:
        artifact = native.manifest.artifacts[0]
        assert artifact.purpose == "native-trace"
        assert artifact.chunk_sequences == [2]
        assert artifact.owner_sequence == 3
        assert {path.name for path in native.root.rglob("*")} == {
            "command.json",
            "records",
            "000000.ndjson",
            "manifest.json",
        }


@pytest.mark.parametrize("defect", ["missing-chunk", "bad-hash", "duplicate-terminal"])
def test_native_artifact_bad_closure_never_creates_host_manifest(tmp_path, monkeypatch, defect):
    command, _lines, key, plan = _input(monkeypatch)
    rows = [json.loads(line) for line in _trace_lines()]
    if defect == "missing-chunk":
        rows[1]["observation"]["chunk_index"] = 1
    elif defect == "bad-hash":
        rows[2]["observation"]["retained_sha256"] = "0" * 64
    else:
        duplicate = dict(rows[2])
        duplicate["sequence"] = 4
        rows.insert(3, duplicate)
        rows[-1]["sequence"] = 5
        rows[-1]["observation"]["records"] = 5
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for row in rows:
            writer.append_record(_canonical(row) + b"\n")
        with pytest.raises(ValueError, match=r"artifact|native trace"):
            writer.finish_complete()
        assert ledger.records("native-close") == ()
    assert not (root / f"native-{key}" / "manifest.json").exists()


@pytest.mark.parametrize("wrong", [None, "0" * 64])
def test_native_command_requires_exact_preregistration(tmp_path, monkeypatch, wrong):
    command, _lines, _key, plan = _input(monkeypatch)
    plan = {"native_commands": [] if wrong is None else [wrong]}
    with AttemptLedger.create(tmp_path / "attempt", plan) as ledger:
        with pytest.raises(ValueError, match="absent from immutable host plan"):
            NativeHostWriter.create(ledger, command)
        assert ledger.records("native-command") == ()


@pytest.mark.parametrize(
    "entries",
    [
        lambda key: "prefix-" + key + "-suffix",
        lambda key: {key: True},
        lambda key: [key, key],
        lambda key: [key.upper()],
    ],
)
def test_native_command_rejects_untyped_or_duplicate_durable_registration(
    tmp_path, monkeypatch, entries
):
    command, _lines, key, _plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    plan = {"native_commands": entries(key)}
    with AttemptLedger.create(root, plan) as ledger:
        with pytest.raises(ValueError, match="exact immutable native command list required"):
            NativeHostWriter.create(ledger, command)
        assert ledger.records("native-command") == ()
        assert not (root / f"native-{key}").exists()
    with pytest.raises(ValueError, match="exact immutable native command list required"):
        reopen_native_commitment(root, plan, key)


def test_native_command_rejects_mutated_open_plan_before_any_effect(tmp_path, monkeypatch):
    command, _lines, key, _plan = _input(monkeypatch)
    root = tmp_path / "attempt"
    durable = {"native_commands": []}
    with AttemptLedger.create(root, durable) as ledger:
        ledger.plan["native_commands"].append(key)
        with pytest.raises(ValueError, match="immutable host plan changed after open"):
            NativeHostWriter.create(ledger, command)
        assert ledger.records("host-clock") == ()
        assert ledger.records("native-command") == ()
        assert not (root / f"native-{key}").exists()
    with pytest.raises(ValueError, match="absent from immutable host plan"):
        reopen_native_commitment(root, durable, key)


def test_native_shard_rotation_preserves_contiguous_host_offsets(tmp_path, monkeypatch):
    command, lines, key, plan = _input(monkeypatch)
    monkeypatch.setattr(native_transport, "SHARD_BYTES", max(map(len, lines)) + 1)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for line in lines:
            writer.append_record(line)
        writer.finish_complete()
    with _open_native(root, plan, key) as native:
        assert [shard.records for shard in native.manifest.shards] == [1, 1]
    with AttemptLedger.open(root, plan) as reopened:
        assert [ack["body"]["shard"] for ack in reopened.records("native-record-ack")] == [0, 1]


def test_foreign_ledger_and_duplicate_close_cannot_supply_native_commitment(tmp_path, monkeypatch):
    command, lines, key, plan = _input(monkeypatch)
    source = tmp_path / "source"
    with (
        AttemptLedger.create(source, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for line in lines:
            writer.append_record(line)
        writer.finish_complete()
        ledger.append(
            "native-close",
            {
                "command_sha256": key,
                "manifest_sha256": "0" * 64,
                "last_ack_sequence": 2,
                "state": "complete",
                "host_clock_digest": writer.clock_digest,
            },
        )
    with pytest.raises(ValueError, match="unique closed"):
        reopen_native_commitment(source, plan, key)
    foreign = tmp_path / "foreign"
    with AttemptLedger.create(foreign, plan):
        pass
    with pytest.raises(ValueError, match="unique closed"):
        reopen_native_commitment(foreign, plan, key)


def test_noncanonical_record_and_closed_suffix_never_ack(tmp_path, monkeypatch):
    command, lines, _key, plan = _input(monkeypatch)
    with (
        AttemptLedger.create(tmp_path / "noncanonical", plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        with pytest.raises(ValueError, match="noncanonical"):
            writer.append_record(lines[0][:-1] + b" \n")
        assert ledger.records("native-record-ack") == ()
    with (
        AttemptLedger.create(tmp_path / "suffix", plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        for line in lines:
            writer.append_record(line)
        with pytest.raises(ValueError, match="owner/order"):
            writer.append_record(lines[-1].replace(b'"sequence":2', b'"sequence":3'))
        assert len(ledger.records("native-record-ack")) == 2
        assert ledger.records("native-close") == ()


def test_late_actual_host_ack_keeps_raw_prefix_but_never_returns_ack(tmp_path, monkeypatch):
    command, lines, key, plan = _input(monkeypatch)
    monkeypatch.setattr(native_transport.time, "monotonic_ns", lambda: 101)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as writer,
    ):
        with pytest.raises(ValueError, match="ACK clock/deadline"):
            writer.append_record(lines[0])
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-close") == ()
    assert (root / f"native-{key}" / "records/000000.ndjson").read_bytes() == lines[0]
