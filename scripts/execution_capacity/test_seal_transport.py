"""Exact QGA action, actual helper identity validation and non-replayed phases."""

import json
from uuid import uuid4

import pytest
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.reference_session import GuestSession
from scripts.execution_capacity.test_reference_session import bridge_fields


@pytest.mark.parametrize("fault", [None, "foreign_helper", "foreign_identity", "lost_response"])
def test_fixed_seal_session_binds_helper_and_consumes_unknown(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "host",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )
    identity = {
        key: str(uuid4()) for key in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 100,
            "poll_count_bound": 1000,
        },
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "seal": {
            "phase_timeout_seconds": 30,
            "config_digest": "d" * 64,
            "helper_sha256": "e" * 64,
            "python_sha256": "f" * 64,
            "export": {"protocol_id": "fixture-protocol"},
            "evidence_limits": {
                "bytes_limit": 32 * 1024 * 1024,
                "rows_limit": 65_536,
                "row_limit": 4 * 1024 * 1024,
                "index_bytes": 64 * 1024,
            },
        },
    }

    class Agent:
        def execute(self, action, request):
            assert action.value == "seal"
            assert set(request) == {"identity", "phase", "config_digest"}
            assert request["phase"] == "cleanup"
            self.request = request
            return 51

        def status(self, pid):
            assert pid == 51
            if fault == "lost_response":
                raise EOFError("lost")
            request = self.request
            value = {
                "identity": identity,
                "phase": "cleanup",
                "config_digest": "d" * 64,
                "seal_helper_sha256": "e" * 64,
                "observer_python_sha256": "f" * 64,
                "protocol_id": plan["seal"]["export"]["protocol_id"],
                "evidence_limits": plan["seal"]["evidence_limits"],
                "artifact": {"sha256": "1" * 64, "size_bytes": 123},
                **bridge_fields(pid, "seal", request),
            }
            if fault == "foreign_helper":
                value["seal_helper_sha256"] = "0" * 64
            if fault == "foreign_identity":
                value["identity"] = {**identity, "boot_id": str(uuid4())}
            return json.dumps(value).encode()

    with AttemptLedger.create(tmp_path / "attempt", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(Agent(), ledger, identity)
        if fault:
            with pytest.raises((ValueError, EOFError)):
                session.seal_phase("cleanup")
        else:
            assert session.seal_phase("cleanup")["artifact"]["size_bytes"] == 123
        with pytest.raises(ValueError, match="consumed"):
            session.seal_phase("cleanup")
        with pytest.raises(ValueError, match="fixed"):
            session.seal_phase("run-shell")
