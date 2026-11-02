"""Pure file/fake-clock tests for failed NativeRecord prefix descriptors."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from scripts.execution_capacity import attempt, native_transport
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.native_failed_records import (
    NativeFailedRecordArtifact,
    derive_failed_record_inventory,
    verify_failed_record_inventory,
)
from scripts.execution_capacity.native_failure_transport import NativeFailureDrainWriter
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter

FIXTURE = Path("e2e/performance/native-wire-fixture-v1.json")


def _setup(monkeypatch):
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
    key = hashlib.sha256(command).hexdigest()
    return fixture, command, key, {"native_commands": [key]}


def _record(fixture, sequence, observation):
    return {
        **fixture["records"][0],
        "sequence": sequence,
        "received_ns": str(20 + sequence * 5),
        "observation": observation,
    }


def _chunk(purpose, artifact_id, index, raw):
    if purpose == "image":
        return {
            "kind": "image",
            "capture_id": artifact_id,
            "chunk_index": index,
            "data": base64.b64encode(raw).decode(),
        }
    return {
        "kind": "private-chunk",
        "purpose": purpose,
        "artifact_id": artifact_id,
        "chunk_index": index,
        "data": base64.b64encode(raw).decode(),
    }


def _trace_completion(raw, *, parser="complete"):
    return {
        "kind": "trace-completion",
        "artifact_id": "native-trace",
        "stream_id": "stream" if parser == "complete" else None,
        "end_dispatched_ns": "25",
        "end_received_ns": "26" if parser == "complete" else None,
        "complete_received_ns": "27" if parser == "complete" else None,
        "eof_received_ns": "28" if parser == "complete" else None,
        "data_loss": parser != "complete",
        "parser": parser,
        "observed_bytes": len(raw),
        "retained_bytes": len(raw),
        "retained_chunks": int(bool(raw)),
        "retained_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _host_prefix(root, monkeypatch, observations):
    fixture, command, key, plan = _setup(monkeypatch)
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for sequence, observation in enumerate(observations, 1):
            native.append_record(_canonical(_record(fixture, sequence, observation)) + b"\n")
        with NativeFailureDrainWriter.create(native, wire_version=2):
            pass
    return key, plan


def test_zero_record_prefix_has_empty_exhaustive_inventory(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(root, monkeypatch, [])
    assert derive_failed_record_inventory(root, plan, key) == ()
    assert verify_failed_record_inventory(root, plan, key, []) == ()
    assert list((root / f"native-{key}" / "records").iterdir()) == []


def test_failed_inventory_interleaved_prefix_closed_trace_and_failed_status(tmp_path, monkeypatch):
    raw = b'{"traceEvents":[]}'
    observations = [
        _chunk("rejected-capture", "capture-source", 0, b"rejected"),
        _chunk("native-trace", "native-trace", 0, raw),
        _trace_completion(raw),
        {"kind": "closed", "outcome": "failed", "records": 4},
    ]
    root = tmp_path / "attempt"
    key, plan = _host_prefix(root, monkeypatch, observations)
    descriptors = derive_failed_record_inventory(root, plan, key)
    assert [(item.ordinal, item.purpose, item.state) for item in descriptors] == [
        (0, "rejected-capture", "prefix"),
        (1, "native-trace", "closed"),
    ]
    assert descriptors[0].chunk_sequences == [1]
    assert descriptors[0].owner_sequence is None
    assert descriptors[1].chunk_sequences == [2]
    assert descriptors[1].owner_sequence == 3
    assert descriptors[1].sha256 == hashlib.sha256(raw).hexdigest()
    assert (
        verify_failed_record_inventory(root, plan, key, [item.model_dump() for item in descriptors])
        == descriptors
    )


def test_failed_image_and_trace_prefixes_need_no_terminal_owner(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(
        root,
        monkeypatch,
        [
            _chunk("image", "image-source", 0, b"image-prefix"),
            _chunk("native-trace", "native-trace", 0, b"trace-prefix"),
        ],
    )
    descriptors = derive_failed_record_inventory(root, plan, key)
    assert [(item.purpose, item.state, item.owner_sequence) for item in descriptors] == [
        ("image", "prefix", None),
        ("native-trace", "prefix", None),
    ]


def test_failed_image_terminal_closes_only_the_record_byte_inventory(tmp_path, monkeypatch):
    raw = b"record-byte-inventory"
    sha = hashlib.sha256(raw).hexdigest()
    capture = {
        "kind": "capture",
        "capture_id": "image-source",
        "request_id": "request",
        "target_id": "target",
        "readback_sequence": 1,
        "renderer_candidates": [{"pid": 1, "start": "renderer"}],
        "dispatched_ns": "21",
        "received_ns": "22",
        "postcheck_ns": "23",
        "sha256": sha,
        "bytes": len(raw),
        "chunks": 1,
        "width": 1440,
        "height": 900,
        "qualification": "pending-runtime-qualification",
        "retention": "full",
        "retained_sha256": sha,
        "retained_bytes": len(raw),
        "crop_rect": None,
        "transform": "png-native-full-v1",
        "channels": 3,
    }
    root = tmp_path / "attempt"
    key, plan = _host_prefix(root, monkeypatch, [_chunk("image", "image-source", 0, raw), capture])
    (descriptor,) = derive_failed_record_inventory(root, plan, key)
    assert (descriptor.purpose, descriptor.state, descriptor.owner_sequence) == (
        "image",
        "closed",
        2,
    )
    assert descriptor.sha256 == sha


def test_zero_byte_failed_trace_terminal_is_explicit_closed_artifact(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(root, monkeypatch, [_trace_completion(b"", parser="failed")])
    (descriptor,) = derive_failed_record_inventory(root, plan, key)
    assert descriptor.purpose == "native-trace"
    assert descriptor.state == "closed"
    assert descriptor.size_bytes == 0
    assert descriptor.chunk_sequences == []
    assert descriptor.owner_sequence == 1


@pytest.mark.parametrize("defect", ["omitted", "duplicate", "reversed", "sha", "owner", "ordinal"])
def test_failed_inventory_rejects_nonexhaustive_or_forged_descriptors(
    tmp_path, monkeypatch, defect
):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(
        root,
        monkeypatch,
        [
            _chunk("rejected-capture", "capture-source", 0, b"capture"),
            _chunk("native-trace", "native-trace", 0, b"trace"),
        ],
    )
    rows = [item.model_dump() for item in derive_failed_record_inventory(root, plan, key)]
    if defect == "omitted":
        rows.pop()
    elif defect == "duplicate":
        rows.append(dict(rows[0]))
    elif defect == "reversed":
        rows.reverse()
    elif defect == "sha":
        rows[0]["sha256"] = "0" * 64
    elif defect == "owner":
        rows[1]["owner_sequence"] = 2
        rows[1]["state"] = "closed"
    else:
        rows[0]["ordinal"] = 1
    with pytest.raises(ValueError, match=r"failed record|rejected capture|validation error"):
        verify_failed_record_inventory(root, plan, key, rows)


@pytest.mark.parametrize("defect", ["chunk-index", "short-then-more", "base64", "success-status"])
def test_failed_inventory_rejects_bad_durable_record_semantics(tmp_path, monkeypatch, defect):
    first = _chunk("rejected-capture", "capture-source", 0, b"first")
    observations = [first]
    if defect == "chunk-index":
        first["chunk_index"] = 1
    elif defect == "short-then-more":
        observations.append(_chunk("rejected-capture", "capture-source", 1, b"second"))
    elif defect == "base64":
        first["data"] = "YR=="  # decodes to b'a' but is not canonical base64
    else:
        observations.append({"kind": "closed", "outcome": "observations-closed", "records": 2})
    root = tmp_path / "attempt"
    key, plan = _host_prefix(root, monkeypatch, observations)
    with pytest.raises(ValueError, match=r"failed record|successful or false terminal"):
        derive_failed_record_inventory(root, plan, key)


def test_failed_inventory_rejects_unacknowledged_last_shard_suffix(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(
        root, monkeypatch, [_chunk("rejected-capture", "capture-source", 0, b"capture")]
    )
    path = root / f"native-{key}" / "records" / "000000.ndjson"
    with path.open("ab") as stream:
        stream.write(b"X")
    with pytest.raises(ValueError, match="unacknowledged shard suffix"):
        derive_failed_record_inventory(root, plan, key)


@pytest.mark.parametrize("defect", ["foreign-owner", "changed-bytes", "missing-shard"])
def test_failed_inventory_rechecks_durable_record_ownership_and_physical_copy(
    tmp_path, monkeypatch, defect
):
    root = tmp_path / "attempt"
    key, plan = _host_prefix(
        root, monkeypatch, [_chunk("rejected-capture", "capture-source", 0, b"capture")]
    )
    path = root / f"native-{key}" / "records" / "000000.ndjson"
    if defect == "missing-shard":
        path.unlink()
    elif defect == "changed-bytes":
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"Y2FwdHVyZQ==", b"Zm9yZ2VkIQ=="))
    else:
        row = json.loads(path.read_bytes())
        row["sample_id"] = "foreign"
        changed = _canonical(row) + b"\n"
        path.write_bytes(changed)
        journal = root / "attempt.jsonl"
        rows = [json.loads(line) for line in journal.read_bytes().splitlines()]
        next(item for item in rows if item["kind"] == "native-record-ack")["body"].update(
            {"bytes": len(changed), "sha256": hashlib.sha256(changed).hexdigest()}
        )
        previous = attempt.digest(plan)
        encoded = []
        for sequence, item in enumerate(rows, 1):
            bare = {
                "sequence": sequence,
                "previous": previous,
                "kind": item["kind"],
                "body": item["body"],
            }
            previous = attempt.digest(bare)
            encoded.append(attempt.encode({**bare, "digest": previous}) + b"\n")
        journal.write_bytes(b"".join(encoded))
        with AttemptLedger.open(root, plan):
            pass
    with pytest.raises(
        (ValueError, OSError), match=r"native failure source|failed native record|No such file"
    ):
        derive_failed_record_inventory(root, plan, key)


def test_failed_record_descriptor_zero_bytes_only_closed_trace():
    with pytest.raises(ValueError, match="zero-byte failed record"):
        NativeFailedRecordArtifact(
            ordinal=0,
            purpose="rejected-capture",
            artifact_id="capture",
            state="prefix",
            sha256=hashlib.sha256(b"").hexdigest(),
            size_bytes=0,
            chunk_sequences=[],
            owner_sequence=None,
        )
