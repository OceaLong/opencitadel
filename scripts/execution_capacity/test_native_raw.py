"""Bounded raw native transport fixtures shared with the TypeScript wire gate."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget

FIXTURE = Path("e2e/performance/native-wire-fixture-v1.json")


def _write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def _json(value):
    from scripts.execution_capacity.native_raw import _canonical

    return _canonical(value)


def _fixture(
    tmp_path, *, rows=None, command=None, host_ack=2, extra=False, artifacts=(), failure=None
):
    from scripts.execution_capacity.native_raw import NativeHostCommitment

    value = json.loads(FIXTURE.read_bytes())
    root = tmp_path / "native-raw"
    root.mkdir(mode=0o700)
    command = _write(root / "command.json", _json(value["command"] if command is None else command))
    selected = value["records"] if rows is None else rows
    raw = b"".join(_json(row) + b"\n" for row in selected)
    shard = _write(root / "records/000000.ndjson", raw)
    manifest = {
        "schema": "opencitadel.native-evidence.v2",
        "state": "failed" if failure is not None else "complete",
        "command": command,
        "shards": [
            {
                **shard,
                "path": "records/000000.ndjson",
                "ordinal": 0,
                "first_sequence": 1,
                "last_sequence": len(selected),
                "records": len(selected),
            }
        ],
        "artifacts": [],
        "failure": _write(root / "failure.json", _json(failure)) if failure is not None else None,
    }
    for ordinal, (purpose, artifact_id, content, owner_sequence) in enumerate(artifacts):
        sequences = [
            row["sequence"]
            for row in selected
            if (
                purpose == "image"
                and row["observation"]["kind"] == "image"
                and row["observation"]["capture_id"] == artifact_id
            )
            or (
                purpose != "image"
                and row["observation"]["kind"] == "private-chunk"
                and row["observation"]["purpose"] == purpose
                and row["observation"]["artifact_id"] == artifact_id
            )
        ]
        manifest["artifacts"].append(
            {
                "ordinal": ordinal,
                "purpose": purpose,
                "artifact_id": artifact_id,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "chunk_sequences": sequences,
                "owner_sequence": owner_sequence,
            }
        )
    manifest_raw = _json(manifest)
    _write(root / "manifest.json", manifest_raw)
    if extra:
        _write(root / "foreign.bin", b"foreign")
    host = NativeHostCommitment(
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        command_sha256=command["sha256"],
        last_ack_sequence=host_ack,
        state=manifest["state"],
    )
    return root, host


def _open(root, host):
    from scripts.execution_capacity.native_raw import NativeEvidenceSession

    return NativeEvidenceSession.open(
        root,
        host=host,
        budget=EvidenceBudget(
            bytes_limit=64 * 1024 * 1024, rows_limit=100_000, row_limit=4 * 1024 * 1024
        ),
        index_bytes=256 * 1024,
    )


def test_shared_native_wire_fixture_indexes_complete_host_bound_prefix(tmp_path):
    root, host = _fixture(tmp_path)
    with _open(root, host) as native:
        assert [row.sequence for row in native.records()] == [1, 2]
        assert native.command.sample_id == "sample"
        assert native.structural_complete is True
        assert native.full_source_ready is False  # No actual trace/image/semantic joins.
        copied = native.copy(tmp_path / "copied-native")
        with copied:
            assert [row.sequence for row in copied.records()] == [1, 2]


def test_js_canonical_unicode_controls_and_number_edges_keep_owner_identity(tmp_path):
    from scripts.execution_capacity.native_raw import _canonical

    assert (
        _canonical({"id": "\ud800😀\t\u0000", "n": 1.0})
        == b'{"id":"\\ud800\xf0\x9f\x98\x80\\t\\u0000","n":1}'
    )
    assert (
        _canonical([1e-6, 1e-7, 1e20, 1e21, -0.0])
        == b"[0.000001,1e-7,100000000000000000000,1e+21,0]"
    )
    value = json.loads(FIXTURE.read_bytes())
    unusual = "😀\t\u0000"
    value["command"]["sample_id"] = unusual
    for row in value["records"]:
        row["sample_id"] = unusual
    root, host = _fixture(tmp_path, rows=value["records"], command=value["command"])
    with _open(root, host) as session:
        assert session.command.sample_id == unusual
        assert [record.sample_id for record in session.records()] == [unusual, unusual]


def test_native_wire_lone_surrogate_fails_shared_python_model(tmp_path):
    value = json.loads(FIXTURE.read_bytes())
    value["command"]["sample_id"] = "\ud800"
    for row in value["records"]:
        row["sample_id"] = "\ud800"
    root, host = _fixture(tmp_path, rows=value["records"], command=value["command"])
    with pytest.raises(ValueError, match="string_unicode"):
        _open(root, host)


@pytest.mark.parametrize("defect", ["reorder", "missing", "foreign", "late_ack", "extra"])
def test_native_raw_rejects_gaps_foreign_owner_late_ack_and_foreign_file(tmp_path, defect):
    value = json.loads(FIXTURE.read_bytes())
    rows = value["records"]
    if defect == "reorder":
        rows = rows[::-1]
    elif defect == "missing":
        rows = rows[:1]
    elif defect == "foreign":
        rows[1]["sample_id"] = "other"
    root, host = _fixture(
        tmp_path, rows=rows, host_ack=1 if defect == "late_ack" else 2, extra=defect == "extra"
    )
    with pytest.raises(ValueError, match=r"native|validation error|private proof"):
        _open(root, host)


def _trace_rows():
    value = json.loads(FIXTURE.read_bytes())
    command = value["command"]
    first = value["records"][0]
    raw = b'{"traceEvents":[]}'
    image = {
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
    completed = {
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
    closed = {**value["records"][-1], "sequence": 4, "received_ns": "26"}
    closed["observation"] = {**closed["observation"], "records": 4}
    assert command["deadline_ns"] == "100"
    return [first, image, completed, closed], raw


@pytest.mark.parametrize(
    "defect",
    [None, "chunk-missing", "chunk-reordered", "descriptor-corrupt", "owner-wrong", "late-ack"],
)
def test_native_trace_raw_chunk_chain_and_completion(tmp_path, defect):
    rows, raw = _trace_rows()
    if defect == "chunk-missing":
        rows[1]["observation"]["data"] = base64.b64encode(raw[:-1]).decode()
    elif defect == "chunk-reordered":
        rows[1]["observation"]["chunk_index"] = 1
    elif defect == "owner-wrong":
        rows[2]["observation"]["artifact_id"] = "foreign"
    root, host = _fixture(
        tmp_path,
        rows=rows,
        host_ack=3 if defect == "late-ack" else 4,
        artifacts=[("native-trace", "native-trace", raw, 2 if defect == "owner-wrong" else 3)],
    )
    if defect == "descriptor-corrupt":
        from scripts.execution_capacity.native_raw import NativeHostCommitment

        path = root / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["artifacts"][0]["sha256"] = "0" * 64
        raw_manifest = _json(manifest)
        path.write_bytes(raw_manifest)
        host = NativeHostCommitment(
            manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
            command_sha256=host.command_sha256,
            last_ack_sequence=host.last_ack_sequence,
            state=host.state,
        )
    if defect is None:
        with _open(root, host) as session:
            assert [record.sequence for record in session.records()] == [1, 2, 3, 4]
            with session.copy(tmp_path / "copied-trace") as copied:
                assert copied.manifest.artifacts[0].sha256 == hashlib.sha256(raw).hexdigest()
    else:
        with pytest.raises(ValueError, match=r"native|validation error|private proof"):
            _open(root, host)


def test_native_v2_logical_chunk_locators_allow_interleaving_without_binary_sidecar(tmp_path):
    value = json.loads(FIXTURE.read_bytes())
    first = value["records"][0]
    raw = b'{"traceEvents":[' + b" " * 50000 + b"]}"
    records = [first]
    for sequence, index, chunk in ((2, 0, raw[:49152]), (4, 1, raw[49152:])):
        records.append(
            {
                **first,
                "sequence": sequence,
                "received_ns": str(10 + sequence),
                "observation": {
                    "kind": "private-chunk",
                    "artifact_id": "native-trace",
                    "purpose": "native-trace",
                    "chunk_index": index,
                    "data": base64.b64encode(chunk).decode(),
                },
            }
        )
    interleaved = {**first, "sequence": 3, "received_ns": "13"}
    records.insert(2, interleaved)
    completion = {
        **first,
        "sequence": 5,
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
            "retained_chunks": 2,
            "retained_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    closed = {**value["records"][-1], "sequence": 6, "received_ns": "26"}
    closed["observation"] = {**closed["observation"], "records": 6}
    records.extend((completion, closed))
    root, host = _fixture(
        tmp_path,
        rows=records,
        host_ack=6,
        artifacts=[("native-trace", "native-trace", raw, 5)],
    )
    assert not (root / "binary").exists()
    with _open(root, host) as session:
        assert session.manifest.artifacts[0].chunk_sequences == [2, 4]
        with session.copy(tmp_path / "copy-single-physical") as copied:
            assert not (copied.root / "binary").exists()
            assert copied.manifest.artifacts[0].chunk_sequences == [2, 4]


@pytest.mark.parametrize(
    "defect", ["wrong-sequence", "duplicate-sequence", "missing-sequence", "terminal-before-chunk"]
)
def test_native_v2_manifest_rejects_wrong_logical_chunk_closure(tmp_path, defect):
    from scripts.execution_capacity.native_raw import NativeHostCommitment

    rows, raw = _trace_rows()
    root, host = _fixture(
        tmp_path,
        rows=rows,
        host_ack=4,
        artifacts=[("native-trace", "native-trace", raw, 3)],
    )
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    artifact = manifest["artifacts"][0]
    if defect == "wrong-sequence":
        artifact["chunk_sequences"] = [3]
    elif defect == "duplicate-sequence":
        artifact["chunk_sequences"] = [2, 2]
    elif defect == "missing-sequence":
        artifact["chunk_sequences"] = []
    else:
        artifact["owner_sequence"] = 2
    encoded = _json(manifest)
    path.write_bytes(encoded)
    host = NativeHostCommitment(
        manifest_sha256=hashlib.sha256(encoded).hexdigest(),
        command_sha256=host.command_sha256,
        last_ack_sequence=host.last_ack_sequence,
        state=host.state,
    )
    with pytest.raises(ValueError, match=r"native|validation error"):
        _open(root, host)


def test_native_v2_copy_detects_original_mutation_and_keeps_prefix(tmp_path):
    rows, raw = _trace_rows()
    root, host = _fixture(
        tmp_path,
        rows=rows,
        host_ack=4,
        artifacts=[("native-trace", "native-trace", raw, 3)],
    )
    destination = tmp_path / "incomplete-copy"
    with _open(root, host) as session:
        shard = root / "records/000000.ndjson"
        changed = bytearray(shard.read_bytes())
        changed[-2] ^= 1
        shard.write_bytes(changed)
        with pytest.raises(ValueError, match="native indexed original identity changed"):
            session.record(2)
        with pytest.raises(ValueError, match="private proof original bytes changed"):
            session.copy(destination)
    assert (destination / "manifest.json").exists()
    assert (destination / "command.json").exists()
    assert not (destination / "failure.json").exists()


@pytest.mark.parametrize("defect", [None, "crop", "bad-png", "wrong-channel", "missing-chunk"])
def test_native_capture_retained_png_and_crop_geometry(tmp_path, defect):
    from scripts.execution_capacity.test_native_png import png

    value = json.loads(FIXTURE.read_bytes())
    first = value["records"][0]
    crop = defect == "crop"
    raw = png(320, 64, channels=4) if crop else png()
    if defect == "bad-png":
        raw = raw[:25] + bytes([raw[25] ^ 1]) + raw[26:]
    digest = hashlib.sha256(raw).hexdigest()
    image = {
        **first,
        "sequence": 2,
        "received_ns": "18",
        "observation": {
            "kind": "image",
            "capture_id": "capture-1",
            "chunk_index": 0,
            "data": base64.b64encode(raw).decode(),
        },
    }
    capture = {
        **first,
        "sequence": 3,
        "received_ns": "22",
        "observation": {
            "kind": "capture",
            "capture_id": "capture-1",
            "request_id": "screenshot-capture-1",
            "target_id": "target",
            "readback_sequence": 1,
            "renderer_candidates": [{"pid": 1, "start": "1"}],
            "dispatched_ns": "19",
            "received_ns": "20",
            "postcheck_ns": "21",
            "sha256": digest,
            "bytes": len(raw),
            "chunks": 1,
            "width": 1440,
            "height": 900,
            "qualification": "matched-reviewed-build" if crop else "pending-runtime-qualification",
            "retention": "progress-region" if crop else "full",
            "retained_sha256": digest,
            "retained_bytes": len(raw),
            "crop_rect": [4, 4, 320, 64] if crop else None,
            "transform": "png-lossless-text-hull-pad4-v1" if crop else "png-native-full-v1",
            "channels": 3
            if defect == "wrong-channel" and crop
            else (4 if crop else 4 if defect == "wrong-channel" else 3),
        },
    }
    closed = {**value["records"][-1], "sequence": 4, "received_ns": "23"}
    closed["observation"] = {**closed["observation"], "records": 4}
    rows = [first, image, capture, closed]
    if defect == "missing-chunk":
        rows[1]["observation"]["data"] = base64.b64encode(raw[:-1]).decode()
    root, host = _fixture(
        tmp_path,
        rows=rows,
        host_ack=4,
        artifacts=[("image", "capture-1", raw, 3)],
    )
    if defect in {None, "crop"}:
        with _open(root, host) as session:
            assert session.manifest.artifacts[0].purpose == "image"
    else:
        with pytest.raises(ValueError, match=r"native|validation error|private proof"):
            _open(root, host)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "foreign-artifact",
        "late-handle",
        "false-settled",
        "retention-clock",
        "missing-artifact",
        "partial-ack-no-ledger",
        "missing-snapshot-artifact",
    ],
)
def test_native_failed_prefix_keeps_late_operation_and_artifact_owner(tmp_path, defect):
    first = json.loads(FIXTURE.read_bytes())["records"][0]
    raw = b"rejected-original-capture"
    chunk = {
        **first,
        "sequence": 2,
        "received_ns": "21",
        "observation": {
            "kind": "private-chunk",
            "artifact_id": "capture-failed",
            "purpose": "rejected-capture",
            "chunk_index": 0,
            "data": base64.b64encode(raw).decode(),
        },
    }
    failure = {
        **{
            key: first[key]
            for key in (
                "wire_version",
                "attempt_id",
                "protocol_id",
                "sample_id",
                "action_id",
                "context_id",
                "page_id",
                "window_id",
                "clock_id",
            )
        },
        "observed_ns": "25",
        "failure_ns": "20",
        "retention_deadline_ns": "10000000020",
        "cause": "capture-failed",
        "discarded_bytes": 0,
        "held_bytes": len(raw),
        "operations": [
            {
                "operation_id": "op",
                "sample_id": "sample",
                "action_id": "action",
                "stage": "capture",
                "started_ns": "15",
                "settled_ns": None,
                "state": "pending",
                "late": False,
                "error_digest": None,
            }
        ],
        "handles": [],
        "artifacts": [
            {
                "artifact_id": "capture-failed",
                "observed_bytes": len(raw),
                "retained_bytes": len(raw),
                "acknowledged_bytes": 0,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "received_ns": "21",
            }
        ],
        "disposition": "pending",
    }
    if defect == "foreign-artifact":
        failure["artifacts"][0]["artifact_id"] = "foreign"
    elif defect == "late-handle":
        failure["handles"] = [{"operation_id": "foreign", "kind": "new-page"}]
    elif defect == "false-settled":
        failure["disposition"] = "settled"
    elif defect == "retention-clock":
        failure["retention_deadline_ns"] = "10000000021"
    elif defect == "partial-ack-no-ledger":
        failure["artifacts"][0]["acknowledged_bytes"] = 1
        failure["held_bytes"] = len(raw) - 1
    elif defect == "missing-snapshot-artifact":
        failure["artifacts"] = []
        failure["held_bytes"] = 0
    artifacts = (
        [] if defect == "missing-artifact" else [("rejected-capture", "capture-failed", raw, None)]
    )
    root, host = _fixture(
        tmp_path, rows=[first, chunk], host_ack=2, artifacts=artifacts, failure=failure
    )
    if defect is None:
        with _open(root, host) as session:
            assert session.structural_complete is False
            assert session.full_source_ready is False
            assert session.manifest.failure is not None
    else:
        with pytest.raises(ValueError, match=r"native|validation error|private proof"):
            _open(root, host)
