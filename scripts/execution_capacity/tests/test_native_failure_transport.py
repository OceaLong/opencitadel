"""Pure file/clock coverage of the independent native failure ACK prefix."""

import base64
import hashlib
import json
import os
import shutil
import tracemalloc
from pathlib import Path

import pytest
from scripts.execution_capacity import attempt, native_failure_transport, native_transport
from scripts.execution_capacity.attempt import AttemptLedger, digest
from scripts.execution_capacity.native_failure_transport import (
    NativeFailureDrainWriter,
    reconcile_failure_snapshot_v2,
    reopen_failure_prefix,
)
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
    monkeypatch.setattr(native_failure_transport.time, "monotonic_ns", lambda: 50)
    fixture = json.loads(FIXTURE.read_bytes())
    command = _canonical(fixture["command"])
    key = hashlib.sha256(command).hexdigest()
    first = _canonical(fixture["records"][0]) + b"\n"
    return command, key, first, {"native_commands": [key]}


def _chunk(content, offset=0, *, artifact_id="capture-failed"):
    fixture = json.loads(FIXTURE.read_bytes())
    command = fixture["command"]
    raw = content[offset : offset + 49152]
    return {
        **{
            key: command[key]
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
        "artifact_id": artifact_id,
        "observed_bytes": len(content),
        "retained_bytes": len(content),
        "retained_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_received_ns": "25",
        "failure_ns": "20",
        "retention_deadline_ns": "10000000020",
        "offset": offset,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data": raw,
    }


def _native(root, plan, command, first):
    ledger = AttemptLedger.create(root, plan)
    native = NativeHostWriter.create(ledger, command)
    native.append_record(first)
    return ledger, native


def _rechain_attempt(root, plan, transform):
    """Construct a valid-chain but semantically false private ledger fixture."""
    path = root / "attempt.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    changed = transform(rows)
    previous = digest(plan)
    output = []
    for sequence, old in enumerate(changed, 1):
        row = {
            "sequence": sequence,
            "previous": previous,
            "kind": old["kind"],
            "body": old["body"],
        }
        previous = digest(row)
        output.append(attempt.encode({**row, "digest": previous}) + b"\n")
    path.write_bytes(b"".join(output))


def _closed_failure_prefix(root, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        drain.append_chunk(_chunk(b"small"))
    return key, plan


def test_zero_failure_ack_is_empty_independent_prefix(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        assert drain.count == 0
        assert ledger.records("native-failure-ack") == ()
    assert reopen_failure_prefix(root, plan, key) == {}
    assert (root / f"native-{key}" / "failure-drain.ndjson").read_bytes() == b""


def test_failure_prefix_rejects_second_durable_host_clock_after_ack(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        drain.append_chunk(_chunk(b"small"))
        ledger.append("host-clock", ledger.records("host-clock")[0]["body"])
    with pytest.raises(ValueError, match="unique native failure host owner absent"):
        reopen_failure_prefix(root, plan, key)


@pytest.mark.parametrize(
    "defect", ["missing", "tampered", "out-of-order", "wrong-root", "wrong-size"]
)
def test_failure_prefix_rejects_false_host_clock_in_valid_attempt_chain(
    tmp_path, monkeypatch, defect
):
    root = tmp_path / "attempt"
    key, plan = _closed_failure_prefix(root, monkeypatch)

    def change(rows):
        index = next(i for i, row in enumerate(rows) if row["kind"] == "host-clock")
        clock = rows[index]
        if defect == "missing":
            return [row for i, row in enumerate(rows) if i != index]
        if defect == "tampered":
            clock["body"]["boot_id"] = "00000000-0000-0000-0000-000000000002"
            return rows
        command_index = next(i for i, row in enumerate(rows) if row["kind"] == "native-command")
        if defect == "wrong-root":
            rows[command_index]["body"]["root"] = "native-foreign"
            return rows
        if defect == "wrong-size":
            rows[command_index]["body"]["size_bytes"] += 1
            return rows
        rows[index], rows[command_index] = rows[command_index], rows[index]
        return rows

    _rechain_attempt(root, plan, change)
    with AttemptLedger.open(root, plan):
        pass
    with pytest.raises(
        ValueError,
        match=r"unique native failure host owner absent|host clock/command/open differs|command bytes differ",
    ):
        reopen_failure_prefix(root, plan, key)


@pytest.mark.parametrize("with_ack", [False, True])
def test_failure_spool_forbids_successful_native_close(tmp_path, monkeypatch, with_ack):
    command, key, _first, plan = _setup(monkeypatch)
    fixture = json.loads(FIXTURE.read_bytes())
    lines = [_canonical(row) + b"\n" for row in fixture["records"]]
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for line in lines:
            native.append_record(line)
        with NativeFailureDrainWriter.create(native) as drain:
            if with_ack:
                drain.append_chunk(_chunk(b"small"))
            with pytest.raises(ValueError, match="failure ownership forbids complete closure"):
                native.finish_complete()
            assert ledger.records("native-close") == ()
    assert reopen_failure_prefix(root, plan, key) == (
        {
            "capture-failed": {
                "metadata": (5, 5, hashlib.sha256(b"small").hexdigest(), "25", "20", "10000000020"),
                "acknowledged_bytes": 5,
                "acknowledged_sha256": hashlib.sha256(b"small").hexdigest(),
            }
        }
        if with_ack
        else {}
    )


def test_partial_and_full_failure_ack_bind_original_whole_artifact(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    content = b"a" * 49152 + b"remaining"
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        first_chunk = _chunk(content)
        receipt = drain.append_chunk(first_chunk)
        assert receipt["durable_receipt_id"] == digest(ledger.records("native-failure-ack")[-1])
        assert receipt["offset"] == 0
        assert receipt["retained_sha256"] == hashlib.sha256(content).hexdigest()
        assert drain.artifacts["capture-failed"]["offset"] == 49152
        second = drain.append_chunk(_chunk(content, 49152))
        assert second["offset"] == 49152
        assert drain.artifacts["capture-failed"]["offset"] == len(content)
        assert drain.count == 2
    prefix = reopen_failure_prefix(root, plan, key)
    assert prefix["capture-failed"]["acknowledged_bytes"] == len(content)
    assert prefix["capture-failed"]["acknowledged_sha256"] == hashlib.sha256(content).hexdigest()
    assert not list((root / f"native-{key}").rglob("*.bin"))


def test_partial_failure_prefix_reopens_and_copy_detects_mutation(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    content = b"a" * 49152 + b"remaining"
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        drain.append_chunk(_chunk(content))
    prefix = reopen_failure_prefix(root, plan, key)
    assert prefix["capture-failed"]["acknowledged_bytes"] == 49152
    source = root / f"native-{key}"
    copied = tmp_path / "copied-native"
    copied.mkdir(mode=0o700)
    for name in ("command.json", "failure-drain.ndjson"):
        (copied / name).write_bytes((source / name).read_bytes())
        (copied / name).chmod(0o600)
    assert reopen_failure_prefix(root, plan, key, copy_root=copied) == prefix
    path = copied / "failure-drain.ndjson"
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    with pytest.raises(ValueError, match="ACKed line changed"):
        reopen_failure_prefix(root, plan, key, copy_root=copied)


@pytest.mark.parametrize("defect", ["offset", "owner", "sha", "metadata"])
def test_bad_failure_chunk_never_gets_host_ack(tmp_path, monkeypatch, defect):
    command, key, first, plan = _setup(monkeypatch)
    content = b"a" * 49152 + b"remaining"
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        chunk = _chunk(content)
        if defect == "offset":
            chunk["offset"] = 1
        elif defect == "owner":
            chunk["sample_id"] = "foreign"
        elif defect == "sha":
            chunk["sha256"] = "0" * 64
        else:
            chunk["retained_bytes"] += 1
        with pytest.raises(ValueError, match="native failure callback"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()
        assert drain.poisoned
    assert reopen_failure_prefix(root, plan, key) == {}


def test_failure_ack_after_deadline_refuses_later_chunk_and_preserves_prefix(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    content = b"a" * 49152 + b"remaining"
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        drain.append_chunk(_chunk(content))
        monkeypatch.setattr(native_failure_transport.time, "monotonic_ns", lambda: 10000000021)
        with pytest.raises(ValueError, match="line differs"):
            drain.append_chunk(_chunk(content, 49152))
        assert len(ledger.records("native-failure-ack")) == 1
    assert reopen_failure_prefix(root, plan, key)["capture-failed"]["acknowledged_bytes"] == 49152


def test_deadline_after_file_fsync_leaves_no_host_ack(tmp_path, monkeypatch):
    command, key, first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        moments = iter((50, 10000000021))
        monkeypatch.setattr(native_failure_transport.time, "monotonic_ns", lambda: next(moments))
        with pytest.raises(ValueError, match="host ACK missed retention deadline"):
            drain.append_chunk(_chunk(b"small"))
        assert ledger.records("native-failure-ack") == ()
    assert (root / f"native-{key}" / "failure-drain.ndjson").stat().st_size > 0
    with pytest.raises(ValueError, match="unacknowledged file suffix"):
        reopen_failure_prefix(root, plan, key)


@pytest.mark.parametrize("defect", ["duplicate", "gap", "changed-metadata", "wrong-total-sha"])
def test_failure_artifact_reuse_or_changed_prefix_never_acks_again(tmp_path, monkeypatch, defect):
    command, key, first, plan = _setup(monkeypatch)
    content = b"a" * 49152 + b"remaining"
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        drain.append_chunk(_chunk(content))
        if defect == "duplicate":
            second = _chunk(content)
        else:
            second = _chunk(content, 49153 if defect == "gap" else 49152)
            if defect == "changed-metadata":
                second["observed_bytes"] += 1
            else:
                if defect == "wrong-total-sha":
                    second["retained_sha256"] = "0" * 64
        with pytest.raises(ValueError, match=r"offset/metadata|fully ACKed"):
            drain.append_chunk(second)
        assert len(ledger.records("native-failure-ack")) == 1
    assert reopen_failure_prefix(root, plan, key)["capture-failed"]["acknowledged_bytes"] == 49152


@pytest.mark.parametrize("ceiling", ["raw", "file", "chunks"])
def test_failure_spool_preflights_all_three_cumulative_limits(tmp_path, monkeypatch, ceiling):
    command, key, first, plan = _setup(monkeypatch)
    if ceiling == "raw":
        monkeypatch.setattr(native_failure_transport, "FAILURE_RAW_BYTES", 4)
    elif ceiling == "file":
        monkeypatch.setattr(native_failure_transport, "FAILURE_FILE_BYTES", 1)
    else:
        monkeypatch.setattr(native_failure_transport, "FAILURE_CHUNKS", 0)
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        with pytest.raises(ValueError, match="quota exceeded"):
            drain.append_chunk(_chunk(b"small"))
        assert ledger.records("native-failure-ack") == ()
    assert (root / f"native-{key}" / "failure-drain.ndjson").read_bytes() == b""


@pytest.mark.parametrize("defect", ["short-write", "host-ack"])
def test_failure_write_or_host_ack_fault_retains_unacknowledged_prefix(
    tmp_path, monkeypatch, defect
):
    command, key, first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, first)
    with ledger, native, NativeFailureDrainWriter.create(native) as drain:
        if defect == "short-write":

            def short_write(descriptor, raw):
                os.write(descriptor, raw[:9])
                raise OSError("injected failure spool short write")

            monkeypatch.setattr(native_failure_transport, "_write_exact", short_write)
        else:
            original = ledger.append

            def fail_ack(kind, body):
                if kind == "native-failure-ack":
                    raise OSError("injected failure ledger ACK")
                return original(kind, body)

            monkeypatch.setattr(ledger, "append", fail_ack)
        with pytest.raises(OSError, match="injected"):
            drain.append_chunk(_chunk(b"small"))
        assert drain.poisoned
        assert ledger.records("native-failure-ack") == ()
    assert (root / f"native-{key}" / "failure-drain.ndjson").stat().st_size > 0
    with pytest.raises(ValueError, match="unacknowledged file suffix"):
        reopen_failure_prefix(root, plan, key)


def _wire2_source(root, monkeypatch, *, purpose="rejected-capture", content=b"source"):
    command, key, _first, plan = _setup(monkeypatch)
    fixture = json.loads(FIXTURE.read_bytes())
    record = fixture["records"][0]
    artifact_id = "original-capture" if purpose == "rejected-capture" else "native-trace"
    observation = {
        "kind": "private-chunk",
        "purpose": purpose,
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "data": base64.b64encode(content).decode(),
    }
    record["observation"] = observation
    ledger, native = _native(root, plan, command, _canonical(record) + b"\n")
    ref = {
        "kind": "native-record",
        "sequence": 1,
        "purpose": purpose,
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "artifact_offset": 0,
        "bytes": len(content),
    }
    chunk = _chunk(content)
    chunk["wire_version"] = 2
    chunk["source_record_ref"] = ref
    return key, plan, ledger, native, chunk


@pytest.mark.parametrize("purpose", ["rejected-capture", "native-trace"])
def test_wire2_native_source_exact_durable_record_bytes(tmp_path, monkeypatch, purpose):
    root = tmp_path / "attempt"
    key, plan, ledger, native, chunk = _wire2_source(root, monkeypatch, purpose=purpose)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        receipt = drain.append_chunk(chunk)
        assert receipt["source_record_ref"] == chunk["source_record_ref"]
        assert receipt["durable_receipt_id"] == digest(ledger.records("native-failure-ack")[-1])
    prefix = reopen_failure_prefix(root, plan, key)
    assert prefix["capture-failed"]["acknowledged_bytes"] == len(chunk["data"])


def test_wire2_rejected_capture_image_record_is_wrong_kind(tmp_path, monkeypatch):
    command, _key, _first, plan = _setup(monkeypatch)
    fixture = json.loads(FIXTURE.read_bytes())
    record = fixture["records"][0]
    record["observation"] = {
        "kind": "image",
        "capture_id": "original-capture",
        "chunk_index": 0,
        "data": base64.b64encode(b"source").decode(),
    }
    root = tmp_path / "attempt"
    ledger, native = _native(root, plan, command, _canonical(record) + b"\n")
    chunk = _chunk(b"source")
    chunk["wire_version"] = 2
    chunk["source_record_ref"] = {
        "kind": "native-record",
        "sequence": 1,
        "purpose": "rejected-capture",
        "artifact_id": "original-capture",
        "chunk_index": 0,
        "artifact_offset": 0,
        "bytes": 6,
    }
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        with pytest.raises(ValueError, match="source record identity differs"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()


def test_wire2_source_replay_retains_locators_not_all_decoded_chunks(tmp_path, monkeypatch):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    template = json.loads(FIXTURE.read_bytes())["records"][0]
    raw = b"x" * 49152
    encoded = base64.b64encode(raw).decode()
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        refs = {}
        for sequence in range(1, 97):
            row = {
                **template,
                "sequence": sequence,
                "observation": {
                    "kind": "private-chunk",
                    "purpose": "rejected-capture",
                    "artifact_id": "original-capture",
                    "chunk_index": sequence - 1,
                    "data": encoded,
                },
            }
            native.append_record(_canonical(row) + b"\n")
            refs[sequence] = native_failure_transport.NativeRecordSourceRef.model_validate(
                {
                    "kind": "native-record",
                    "sequence": sequence,
                    "purpose": "rejected-capture",
                    "artifact_id": "original-capture",
                    "chunk_index": sequence - 1,
                    "artifact_offset": 0,
                    "bytes": len(raw),
                }
            )
        tracemalloc.start()
        try:
            locators = native_failure_transport._verified_record_prefix(
                ledger,
                native.root,
                native.command,
                key,
                native.clock_digest,
                len(ledger.rows) + 1,
                refs,
            )
            for sequence, locator in locators.items():
                assert isinstance(locator, native_failure_transport.NativeRecordAck)
                native_failure_transport._verify_record_source_bytes(
                    native_failure_transport._verified_record_line(
                        native.root, native.command, locator
                    ),
                    refs[sequence],
                    raw,
                )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(locators) == 96
        assert peak < 3 * 1024 * 1024


def test_wire2_independent_zero_record_source_and_partial_prefix(tmp_path, monkeypatch):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    content = b"a" * 49152 + b"tail"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        chunk = _chunk(content)
        chunk["wire_version"] = 2
        chunk["source_record_ref"] = {"kind": "independent"}
        drain.append_chunk(chunk)
    assert reopen_failure_prefix(root, plan, key)["capture-failed"]["acknowledged_bytes"] == 49152


@pytest.mark.parametrize(
    "defect",
    ["sequence", "purpose", "artifact", "chunk", "range", "bytes", "identity", "suffix"],
)
def test_wire2_false_source_never_receives_failure_ack(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    key, plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    ref = chunk["source_record_ref"]
    if defect == "sequence":
        ref["sequence"] = 2
    elif defect == "purpose":
        ref["purpose"] = "native-trace"
    elif defect == "artifact":
        ref["artifact_id"] = "foreign"
    elif defect == "chunk":
        ref["chunk_index"] = 1
    elif defect == "range":
        ref["artifact_offset"] = 1
    elif defect == "bytes":
        chunk["data"] = b"other!"
        chunk["bytes"] = 6
        chunk["sha256"] = hashlib.sha256(b"other!").hexdigest()
        chunk["retained_bytes"] = 6
        chunk["retained_sha256"] = chunk["sha256"]
        chunk["observed_bytes"] = 6
    elif defect == "identity":
        chunk["sample_id"] = "foreign"
    else:
        path = native.root / "records" / "000000.ndjson"
        with path.open("ab") as stream:
            stream.write(b"X")
        with ledger, native:
            with pytest.raises(ValueError, match="unacknowledged shard suffix"):
                NativeFailureDrainWriter.create(native, wire_version=2)
            assert ledger.records("native-failure-open") == ()
        return
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        with pytest.raises(ValueError, match=r"native failure|validation error"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()
    assert reopen_failure_prefix(root, plan, key) == {}


def test_wire2_fresh_copy_rechecks_original_record_bytes(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        drain.append_chunk(chunk)
    copy = tmp_path / "copy"
    shutil.copytree(root / f"native-{key}", copy)
    assert reopen_failure_prefix(root, plan, key, copy_root=copy) == reopen_failure_prefix(
        root, plan, key
    )
    path = copy / "records" / "000000.ndjson"
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"c291cmNl", b"b3RoZXIh"))
    with pytest.raises(ValueError, match="source ACKed line changed"):
        reopen_failure_prefix(root, plan, key, copy_root=copy)


def test_wire2_source_ack_after_failure_open_is_not_prior_source(tmp_path, monkeypatch):
    command, _key, _first, plan = _setup(monkeypatch)
    fixture = json.loads(FIXTURE.read_bytes())
    record = fixture["records"][0]
    record["observation"] = {
        "kind": "private-chunk",
        "purpose": "rejected-capture",
        "artifact_id": "original-capture",
        "chunk_index": 0,
        "data": base64.b64encode(b"source").decode(),
    }
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        native.append_record(_canonical(record) + b"\n")
        chunk = _chunk(b"source")
        chunk["wire_version"] = 2
        chunk["source_record_ref"] = {
            "kind": "native-record",
            "sequence": 1,
            "purpose": "rejected-capture",
            "artifact_id": "original-capture",
            "chunk_index": 0,
            "artifact_offset": 0,
            "bytes": 6,
        }
        with pytest.raises(ValueError, match="source ACK owner/order/clock"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()


def test_wire2_post_open_unacknowledged_record_suffix_blocks_callback(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    _key, _plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        with (native.root / "records" / "000000.ndjson").open("ab") as stream:
            stream.write(b"X")
        with pytest.raises(ValueError, match="unacknowledged shard suffix"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()


@pytest.mark.parametrize("defect", ["owner", "clock", "sequence", "offset", "sha"])
def test_wire2_fresh_reader_rejects_valid_chain_false_record_ack(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    key, plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        drain.append_chunk(chunk)

    def change(rows):
        ack = next(row for row in rows if row["kind"] == "native-record-ack")["body"]
        if defect == "owner":
            ack["command_sha256"] = "0" * 64
        elif defect == "clock":
            ack["host_clock_digest"] = "0" * 64
        elif defect == "sequence":
            ack["sequence"] = 2
        elif defect == "offset":
            ack["offset"] = 1
        else:
            ack["sha256"] = "0" * 64
        return rows

    _rechain_attempt(root, plan, change)
    with AttemptLedger.open(root, plan):
        pass
    with pytest.raises(ValueError, match="native failure source"):
        reopen_failure_prefix(root, plan, key)


def test_wire2_rejects_missing_or_forged_source_ref(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    _key, _plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        del chunk["source_record_ref"]
        with pytest.raises(ValueError, match="source_record_ref"):
            drain.append_chunk(chunk)
        assert ledger.records("native-failure-ack") == ()


@pytest.mark.parametrize("defect", ["owner", "artifact", "data"])
def test_wire2_fresh_reader_rejects_forged_record_even_with_rechained_ack(
    tmp_path, monkeypatch, defect
):
    root = tmp_path / "attempt"
    key, plan, ledger, native, chunk = _wire2_source(root, monkeypatch)
    with ledger, native, NativeFailureDrainWriter.create(native, wire_version=2) as drain:
        drain.append_chunk(chunk)
    path = root / f"native-{key}" / "records" / "000000.ndjson"
    row = json.loads(path.read_bytes())
    if defect == "owner":
        row["sample_id"] = "foreign"
    elif defect == "artifact":
        row["observation"]["artifact_id"] = "foreign"
    else:
        row["observation"]["data"] = base64.b64encode(b"other!").decode()
    changed = _canonical(row) + b"\n"
    path.write_bytes(changed)

    def change(rows):
        ack = next(row for row in rows if row["kind"] == "native-record-ack")["body"]
        ack["bytes"] = len(changed)
        ack["sha256"] = hashlib.sha256(changed).hexdigest()
        return rows

    _rechain_attempt(root, plan, change)
    with pytest.raises(ValueError, match="native failure source"):
        reopen_failure_prefix(root, plan, key)


def _snapshot(*, artifacts=(), discarded=0, disposition=None):
    command = json.loads(FIXTURE.read_bytes())["command"]
    items = list(artifacts)
    held = sum(item["retained_bytes"] - item["acknowledged_bytes"] for item in items)
    return {
        **{name: command[name] for name in native_failure_transport.IDENTITY},
        "wire_version": 2,
        "observed_ns": "50",
        "failure_ns": "20",
        "retention_deadline_ns": "10000000020",
        "cause": "cancelled",
        "discarded_bytes": discarded,
        "held_bytes": held,
        "operations": [],
        "handles": [],
        "artifacts": items,
        "disposition": disposition or ("partial" if held or discarded else "settled"),
    }


def _snapshot_artifact(content, acknowledged=0, *, refs=(), artifact_id="capture-failed"):
    return {
        "artifact_id": artifact_id,
        "observed_bytes": len(content),
        "retained_bytes": len(content),
        "acknowledged_bytes": acknowledged,
        "sha256": hashlib.sha256(content).hexdigest(),
        "received_ns": "25",
        "source_record_refs": list(refs),
    }


def test_wire2_snapshot_zero_ack_zero_record_and_zero_ack_artifact(tmp_path, monkeypatch):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2),
    ):
        pass
    empty = reconcile_failure_snapshot_v2(root, plan, key, _canonical(_snapshot()))
    assert empty["failure_ack_count"] == 0
    assert empty["evidence_state"] == "complete-evidence"
    content = b"unacknowledged"
    partial = reconcile_failure_snapshot_v2(
        root,
        plan,
        key,
        _canonical(_snapshot(artifacts=[_snapshot_artifact(content)])),
    )
    assert partial["evidence_state"] == "partial-evidence"
    assert partial["acknowledged_bytes"] == 0
    assert reopen_failure_prefix(root, plan, key) == {}


@pytest.mark.parametrize("chunks", [1, 2])
def test_wire2_snapshot_independent_partial_and_full_ack(tmp_path, monkeypatch, chunks):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    content = b"a" * 49152 + b"tail"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        for offset in (0, 49152)[:chunks]:
            chunk = _chunk(content, offset)
            chunk["wire_version"] = 2
            chunk["source_record_ref"] = {"kind": "independent"}
            drain.append_chunk(chunk)
    acknowledged = 49152 if chunks == 1 else len(content)
    checked = reconcile_failure_snapshot_v2(
        root,
        plan,
        key,
        _canonical(_snapshot(artifacts=[_snapshot_artifact(content, acknowledged)])),
    )
    assert checked["failure_ack_count"] == chunks
    assert checked["evidence_state"] == ("partial-evidence" if chunks == 1 else "complete-evidence")


def _two_source_prefix(root, monkeypatch, *, chunks=2):
    command, key, _first, plan = _setup(monkeypatch)
    fixture = json.loads(FIXTURE.read_bytes())
    content = b"a" * 49152 + b"tail"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for index, offset in enumerate((0, 49152)):
            row = {
                **fixture["records"][0],
                "sequence": index + 1,
                "received_ns": str(12 + index),
                "observation": {
                    "kind": "private-chunk",
                    "purpose": "rejected-capture",
                    "artifact_id": "source-record",
                    "chunk_index": index,
                    "data": base64.b64encode(content[offset : offset + 49152]).decode(),
                },
            }
            native.append_record(_canonical(row) + b"\n")
        with NativeFailureDrainWriter.create(native, wire_version=2) as drain:
            refs = []
            for index, offset in enumerate((0, 49152)[:chunks]):
                chunk = _chunk(content, offset)
                chunk["wire_version"] = 2
                chunk["source_record_ref"] = {
                    "kind": "native-record",
                    "sequence": index + 1,
                    "purpose": "rejected-capture",
                    "artifact_id": "source-record",
                    "chunk_index": index,
                    "artifact_offset": offset,
                    "bytes": chunk["bytes"],
                }
                drain.append_chunk(chunk)
                refs.append(chunk["source_record_ref"])
    return key, plan, content, refs


def test_wire2_snapshot_binds_distinct_callback_and_record_ids(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, content, refs = _two_source_prefix(root, monkeypatch)
    checked = reconcile_failure_snapshot_v2(
        root,
        plan,
        key,
        _canonical(_snapshot(artifacts=[_snapshot_artifact(content, len(content), refs=refs)])),
    )
    assert checked["failure_ack_count"] == 2
    assert checked["evidence_state"] == "complete-evidence"
    assert refs[0]["artifact_id"] == "source-record"


@pytest.mark.parametrize(
    "defect",
    [
        "ghost",
        "ack-low",
        "ack-high",
        "whole-sha",
        "received",
        "missing-ref",
        "forged-ref",
        "reordered-ref",
        "extra-id",
        "held",
        "disposition",
    ],
)
def test_wire2_snapshot_rejects_two_way_inventory_forgery(tmp_path, monkeypatch, defect):
    root = tmp_path / "attempt"
    key, plan, content, refs = _two_source_prefix(root, monkeypatch)
    artifact = _snapshot_artifact(content, len(content), refs=refs)
    snapshot = _snapshot(artifacts=[artifact])
    if defect == "ghost":
        snapshot["artifacts"] = []
    elif defect == "ack-low":
        artifact["acknowledged_bytes"] = 49152
        snapshot["held_bytes"] = len(content) - 49152
        snapshot["disposition"] = "partial"
    elif defect == "ack-high":
        artifact["acknowledged_bytes"] += 1
    elif defect == "whole-sha":
        artifact["sha256"] = "0" * 64
    elif defect == "received":
        artifact["received_ns"] = "26"
    elif defect == "missing-ref":
        artifact["source_record_refs"] = refs[:1]
    elif defect == "forged-ref":
        artifact["source_record_refs"][1] = {**refs[1], "artifact_id": "foreign"}
    elif defect == "reordered-ref":
        artifact["source_record_refs"] = list(reversed(refs))
    elif defect == "extra-id":
        snapshot["artifacts"].append(_snapshot_artifact(b"extra", 1, artifact_id="extra"))
    elif defect == "held":
        snapshot["held_bytes"] = 1
    else:
        snapshot["disposition"] = "partial"
    with pytest.raises(ValueError, match=r"native failure snapshot|validation error"):
        reconcile_failure_snapshot_v2(root, plan, key, _canonical(snapshot))


def test_wire2_snapshot_rejects_unacked_ref_and_noncanonical_bytes(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    key, plan, content, refs = _two_source_prefix(root, monkeypatch, chunks=1)
    artifact = _snapshot_artifact(content, 49152, refs=refs)
    snapshot = _snapshot(artifacts=[artifact])
    artifact["source_record_refs"].append(
        {**refs[0], "sequence": 2, "chunk_index": 1, "artifact_offset": 49152, "bytes": 4}
    )
    with pytest.raises(ValueError, match="artifact bounds differ"):
        reconcile_failure_snapshot_v2(root, plan, key, _canonical(snapshot))
    artifact["source_record_refs"] = refs
    with pytest.raises(ValueError, match="noncanonical"):
        reconcile_failure_snapshot_v2(root, plan, key, json.dumps(snapshot).encode())


@pytest.mark.parametrize("defect", ["snapshot-v1", "foreign-owner", "prefix-v1"])
def test_wire2_snapshot_requires_matching_version_and_command(tmp_path, monkeypatch, defect):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=1 if defect == "prefix-v1" else 2),
    ):
        pass
    snapshot = _snapshot()
    if defect == "snapshot-v1":
        snapshot["wire_version"] = 1
    elif defect == "foreign-owner":
        snapshot["sample_id"] = "foreign"
    with pytest.raises(ValueError, match=r"native failure snapshot|validation error"):
        reconcile_failure_snapshot_v2(root, plan, key, _canonical(snapshot))


def test_wire2_snapshot_rejects_cumulative_retained_quota(tmp_path, monkeypatch):
    command, key, _first, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2),
    ):
        pass
    size = 70 * 1024 * 1024
    snapshot = _snapshot(
        artifacts=[
            {
                "artifact_id": f"artifact-{index}",
                "observed_bytes": size,
                "retained_bytes": size,
                "acknowledged_bytes": size,
                "sha256": "0" * 64,
                "received_ns": "25",
                "source_record_refs": [],
            }
            for index in range(2)
        ]
    )
    with pytest.raises(ValueError, match="cumulative inventory differs"):
        reconcile_failure_snapshot_v2(root, plan, key, _canonical(snapshot))
