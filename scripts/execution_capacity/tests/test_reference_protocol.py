"""Offline transport/ownership regressions; no socket or process is started."""

import io
import json
from uuid import uuid4

import pytest
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.reference_protocol import JsonChannel, ProtocolError


class Duplex:
    def __init__(self, rows):
        self.input = io.BytesIO(b"".join(json.dumps(r).encode() + b"\r\n" for r in rows))
        self.sent = []

    def recv(self, size):
        return self.input.read(size)

    def sendall(self, value):
        self.sent.append(json.loads(value))

    def settimeout(self, value):
        assert 0 < value <= 2


def test_qmp_retains_events_and_rejects_wrong_response_id():
    wire = Duplex(
        [
            {"QMP": {"version": {}}},
            {"event": "STOP"},
            {"return": {}, "id": 1},
            {"return": {}, "id": 9},
        ]
    )
    channel = JsonChannel(wire, timeout=2)
    channel.negotiate_qmp()
    assert channel.events == [{"event": "STOP"}]
    with pytest.raises(ProtocolError, match="response identity"):
        channel.command("query-status")
    assert wire.sent[0]["execute"] == "qmp_capabilities"


def test_transport_eof_and_oversize_fail_without_retry():
    channel = JsonChannel(Duplex([]), timeout=2)
    with pytest.raises(ProtocolError, match="closed"):
        channel.command("query-status")
    assert channel.poisoned
    with pytest.raises(ProtocolError, match="uncertain"):
        channel.command("query-status")


def test_attempt_consumed_across_reopen_and_foreign_plan_rejected(tmp_path):
    root = tmp_path / "private"
    sample, window = str(uuid4()), str(uuid4())
    plan = {
        "attempt_id": str(uuid4()),
        "samples": [{"sample_id": sample, "window_id": window, "physical_window_id": window}],
    }
    with AttemptLedger.create(root, plan) as ledger:
        ledger.reserve(sample, window, seal_digest="a" * 64)
    with AttemptLedger.open(root, plan) as ledger, pytest.raises(ValueError, match="consumed"):
        ledger.reserve(sample, window, seal_digest="a" * 64)
    with pytest.raises(ValueError, match="plan"):
        AttemptLedger.open(root, {**plan, "attempt_id": str(uuid4())})


def test_torn_ledger_retained(tmp_path):
    root = tmp_path / "private"
    plan = {"attempt_id": str(uuid4()), "samples": []}
    with AttemptLedger.create(root, plan):
        pass
    with (root / "attempt.jsonl").open("ab") as f:
        f.write(b"{")
    with pytest.raises(ValueError, match="incomplete"):
        AttemptLedger.open(root, plan)
    assert (root / "attempt.jsonl").read_bytes().endswith(b"{")


def test_qga_fixed_start_captures_no_output_and_does_not_wait_for_exit():
    from scripts.execution_capacity.reference_protocol import Action, GuestAgent

    wire = Duplex([{"return": {"pid": 33}, "id": 1}])
    agent = GuestAgent(JsonChannel(wire, timeout=2))
    assert agent.execute(Action.START, {"identity": {}}) == 33
    sent = wire.sent[0]["arguments"]
    assert sent["path"] == "/usr/bin/python3"
    assert sent["arg"][:3] == ["-I", "/opt/opencitadel-capacity/guest_bridge.py", "cold-window"]
    assert sent["capture-output"] is False


def test_qga_nonzero_exit_is_failure_and_running_is_not_success():
    from scripts.execution_capacity.reference_protocol import GuestAgent

    wire = Duplex(
        [
            {"return": {"exited": False}, "id": 1},
            {"return": {"exited": True, "exitcode": 3}, "id": 2},
        ]
    )
    agent = GuestAgent(JsonChannel(wire, timeout=2))
    assert agent.status(33) is None
    with pytest.raises(ProtocolError, match="failed"):
        agent.status(33)


def test_shared_native_and_control_threads_preserve_one_ledger_chain(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    plan = {"samples": []}
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: ledger.append("native", {"index": index}), range(40)))
        assert sorted(r["body"]["index"] for r in ledger.rows) == list(range(40))
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        assert len(ledger.rows) == 40
