"""Pure one-command frame to durable host writer checks; no runtime launcher."""

import hashlib
import struct

import pytest
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.native_failed_handoff import (
    ACK,
    CALLBACK,
    HEADER,
    RECORD,
    TERMINAL,
    NativeFailedHandoffConsumer,
    decode_frame,
    encode_frame,
)
from scripts.execution_capacity.native_failure_transport import NativeFailureDrainWriter
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter
from scripts.execution_capacity.test_native_failed_transport import (
    _callback,
    _setup,
    _snapshot,
)


def _host(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    ledger = AttemptLedger.create(tmp_path / "attempt", plan)
    native = NativeHostWriter.create(ledger, command)
    return fixture, key, ledger, native, NativeFailedHandoffConsumer(native)


def _callback_payload(command, content):
    data = _callback(command, content)
    raw = data.pop("data")
    metadata = _canonical(data)
    return struct.pack(">I", len(metadata)) + metadata + raw


def test_frame_vector_exact_bytes_and_truncation():
    key = "00" * 32
    raw = encode_frame(RECORD, 1, key, b"abc\n")
    assert raw == b"OC3B\x01\x00\x00\x00\x01" + bytes(32) + b"\x00\x00\x00\x04abc\n"
    assert decode_frame(raw) == (RECORD, 1, key, b"abc\n")
    for invalid in (raw[:-1], raw + b"x", raw[: HEADER.size - 1]):
        with pytest.raises(ValueError, match="truncated"):
            decode_frame(invalid)
    with pytest.raises(ValueError, match="oversized"):
        decode_frame(raw[:41] + struct.pack(">I", 8 * 1024 * 1024 + 1) + b"abc")
    for invalid_key in ("00" * 31 + "  ", "AA" * 32, "00" * 31 + "0g"):
        with pytest.raises(ValueError, match="exact lower-case command digest"):
            encode_frame(RECORD, 1, invalid_key, b"abc\n")


def test_zero_prefix_keeps_exact_terminal_snapshot_without_close(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    drain = NativeFailureDrainWriter.create(native, wire_version=2)
    consumer.bind_failure_drain(drain)
    try:
        snapshot = _snapshot(fixture["command"])
        ack = consumer.consume(encode_frame(TERMINAL, 1, key, snapshot))
        assert decode_frame(ack) == (ACK, 1, key, hashlib.sha256(snapshot).digest())
        assert consumer.terminal_bytes == snapshot
        assert list((native.root / "records").iterdir()) == []
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-failure-ack") == ()
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "failure.json").exists()
        with pytest.raises(ValueError, match="closed"):
            consumer.consume(encode_frame(TERMINAL, 2, key, snapshot))
    finally:
        drain.close()
        native.close()
        ledger.__exit__(None, None, None)


def test_record_and_callback_ack_are_durable_before_reply(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    drain = None
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        ack = consumer.consume(encode_frame(RECORD, 1, key, line))
        assert decode_frame(ack) == (ACK, 1, key, struct.pack(">I", 1))
        assert (
            ledger.records("native-record-ack")[0]["body"]["sha256"]
            == hashlib.sha256(line).hexdigest()
        )
        drain = NativeFailureDrainWriter.create(native, wire_version=2)
        consumer.bind_failure_drain(drain)
        content = b"x" * 49152 + b"unacknowledged-tail"
        payload = _callback_payload(fixture["command"], content)
        receipt = decode_frame(consumer.consume(encode_frame(CALLBACK, 2, key, payload)))
        assert receipt[:3] == (ACK, 2, key)
        assert b"durable_receipt_id" in receipt[3]
        assert len(ledger.records("native-failure-ack")) == 1
        assert (native.root / "failure-drain.ndjson").stat().st_size > 0
        snapshot = _snapshot(fixture["command"], content=content, acknowledged=49152)
        consumer.consume(encode_frame(TERMINAL, 3, key, snapshot))
        assert consumer.terminal_bytes == snapshot
        assert b"unacknowledged-tail" not in (native.root / "failure-drain.ndjson").read_bytes()
        assert ledger.records("native-failure-close") == ()
    finally:
        if drain is not None:
            drain.close()
        native.close()
        ledger.__exit__(None, None, None)


def test_lost_ack_leaves_one_durable_uncommitted_prefix(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        request = encode_frame(RECORD, 1, key, line)
        consumer.consume(request)  # The caller discards this ACK after a simulated stream loss.
        with pytest.raises(ValueError, match="foreign or duplicate"):
            consumer.consume(request)
        assert consumer.poisoned
        assert len(ledger.records("native-record-ack")) == 1
        assert ledger.records("native-failure-close") == ()
    finally:
        native.close()
        ledger.__exit__(None, None, None)


def test_record_after_failure_drain_is_refused_before_host_write(tmp_path, monkeypatch):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    drain = NativeFailureDrainWriter.create(native, wire_version=2)
    consumer.bind_failure_drain(drain)
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        with pytest.raises(ValueError, match="after failure drain"):
            consumer.consume(encode_frame(RECORD, 1, key, line))
        assert ledger.records("native-record-ack") == ()
        assert consumer.poisoned
    finally:
        drain.close()
        native.close()
        ledger.__exit__(None, None, None)


@pytest.mark.parametrize("defect", ["before-drain", "foreign-snapshot", "noncanonical"])
def test_terminal_frame_requires_bound_drain_and_exact_typed_snapshot(
    tmp_path, monkeypatch, defect
):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    drain = None
    try:
        raw = _snapshot(fixture["command"])
        if defect != "before-drain":
            drain = NativeFailureDrainWriter.create(native, wire_version=2)
            consumer.bind_failure_drain(drain)
        if defect == "foreign-snapshot":
            raw = _snapshot({**fixture["command"], "sample_id": "foreign"})
        elif defect == "noncanonical":
            raw = b" " + raw
        with pytest.raises(ValueError, match=r"drain|foreign|noncanonical"):
            consumer.consume(encode_frame(TERMINAL, 1, key, raw))
        assert consumer.terminal_bytes is None
        assert consumer.poisoned
        assert ledger.records("native-failure-close") == ()
    finally:
        if drain is not None:
            drain.close()
        native.close()
        ledger.__exit__(None, None, None)


@pytest.mark.parametrize(
    "defect", ["duplicate", "wrong-key", "truncated", "oversized", "bad-callback"]
)
def test_invalid_frame_poisoned_without_promotion(tmp_path, monkeypatch, defect):
    fixture, key, ledger, native, consumer = _host(tmp_path, monkeypatch)
    try:
        line = _canonical(fixture["records"][0]) + b"\n"
        first = encode_frame(RECORD, 1, key, line)
        if defect == "duplicate":
            consumer.consume(first)
            bad = first
        elif defect == "wrong-key":
            bad = encode_frame(RECORD, 1, "11" * 32, line)
        elif defect == "truncated":
            bad = first[:-1]
        elif defect == "oversized":
            bad = first[:41] + struct.pack(">I", 8 * 1024 * 1024 + 1) + line
        else:
            bad = encode_frame(CALLBACK, 1, key, b"\x00\x00\x00\x02{}x")
        with pytest.raises(ValueError, match=r"handoff|failure|native"):
            consumer.consume(bad)
        assert consumer.poisoned
        with pytest.raises(ValueError, match="uncertain"):
            consumer.consume(encode_frame(TERMINAL, 2, key, _snapshot(fixture["command"])))
        assert ledger.records("native-failure-close") == ()
    finally:
        native.close()
        ledger.__exit__(None, None, None)
