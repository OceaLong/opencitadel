"""In-memory stream transcripts; never opens/binds network sockets."""

import io

import pytest


class Wire:
    def __init__(self, data=b""):
        self.data = io.BytesIO(data)
        self.sent = bytearray()

    def recv(self, count):
        return self.data.read(min(count, 32768))

    def sendall(self, value):
        self.sent.extend(value)

    def settimeout(self, value):
        pass


def test_fixed_authenticated_echo_rejects_unknown_action_and_wrong_key():
    from scripts.execution_capacity.calibration import request, serve_connection

    key = b"s" * 32
    nonce = b"n" * 16
    wire = Wire(request(key, nonce, "echo") + b"e" * 32)
    serve_connection(wire, key, {"boot_id": "boot", "pid": 8})
    assert wire.sent.endswith(b"e" * 32)
    with pytest.raises(ValueError, match="authentication"):
        serve_connection(Wire(request(b"x" * 32, nonce, "echo")), key, {})
    with pytest.raises(ValueError, match="fixed"):
        request(key, nonce, "proxy")


def test_measured_stream_derives_rate_from_actual_elapsed_bytes(monkeypatch):
    from scripts.execution_capacity import calibration as mod

    key, nonce = b"s" * 32, b"n" * 16
    identity = {"boot_id": "boot", "pid": 8}
    metadata = mod.response_header(key, nonce, identity)
    wire = Wire(metadata + b"\0" * (8 * 1024**2))
    clock = iter([1_000_000_000, 5_000_000_000])
    monkeypatch.setattr(mod.time, "monotonic_ns", lambda: next(clock))
    observed = mod.measure(wire, key, nonce, "download", identity)
    assert observed["bytes"] == 8388608
    assert observed["elapsed_ns"] == 4000000000
    assert observed["bits_per_second"] == 16777216
    assert observed["server"] == identity


def test_response_truncation_or_identity_mismatch_never_becomes_measurement():
    from scripts.execution_capacity import calibration as mod

    key, nonce = b"s" * 32, b"n" * 16
    with pytest.raises(ValueError, match="identity"):
        mod.measure(
            Wire(mod.response_header(key, nonce, {"pid": 9})), key, nonce, "echo", {"pid": 8}
        )
    with pytest.raises(EOFError):
        mod.measure(
            Wire(mod.response_header(key, nonce, {"pid": 8}) + b"short"),
            key,
            nonce,
            "echo",
            {"pid": 8},
        )


def test_validity_uses_separate_observed_echo_and_transfer_intervals():
    from scripts.execution_capacity.reference_calibration import assess

    rows = [{"action": "echo", "echo_rtt_ns": 53_000_000}] * 16 + [
        {"action": "upload", "bits_per_second": 18_000_000},
        {"action": "download", "bits_per_second": 19_000_000},
    ]
    rule = {
        "added_rtt_min_ns": 40_000_000,
        "added_rtt_max_ns": 70_000_000,
        "throughput_min_bps": 15_000_000,
        "throughput_max_bps": 21_000_000,
    }
    baseline = {"rows": [{"action": "echo", "echo_rtt_ns": 3_000_000}] * 16, "errors": []}
    result = {"rows": rows, "errors": [], "expected": 18, "attempted": 18}
    assert assess(result, baseline, rule)["observed_added_median_rtt_ns"] == 50_000_000
    result["rows"] = rows[:-1]
    with pytest.raises(ValueError, match="incomplete"):
        assess(result, baseline, rule)


def test_absolute_probe_deadline_cannot_be_extended_by_partial_reads(monkeypatch):
    from scripts.execution_capacity import calibration as mod

    now = [0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    wire = mod.DeadlineWire(Wire(b"abcdef"), 15)
    assert wire.recv(1) == b"a"
    now[0] = 16
    with pytest.raises(TimeoutError, match="deadline"):
        wire.recv(1)
