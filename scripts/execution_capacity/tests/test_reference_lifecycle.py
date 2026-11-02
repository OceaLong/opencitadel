"""Effect-free regression tests: physical Linux boundaries are substituted."""

from uuid import uuid4

import pytest
from scripts.execution_capacity.attempt import AttemptLedger


def test_ledger_reopen_rejects_new_boot_and_retains_original_clock(tmp_path, monkeypatch):
    import scripts.execution_capacity.attempt as module

    observed = {
        "boot_id": str(uuid4()),
        "clock": "CLOCK_MONOTONIC",
        "namespace_device": 1,
        "namespace_inode": 2,
    }
    monkeypatch.setattr(module, "host_clock", lambda: observed, raising=False)
    plan = {"samples": []}
    with AttemptLedger.create(tmp_path / "attempt", plan) as ledger:
        ledger.bind_clock()
        ledger.append("receipt", {"host_ns": 100})
    original = dict(observed)
    observed["boot_id"] = str(uuid4())
    with AttemptLedger.open(tmp_path / "attempt", plan) as ledger:
        with pytest.raises(ValueError, match="clock domain"):
            ledger.bind_clock()
        assert ledger.records("host-clock")[0]["body"] == original
        assert ledger.records("receipt")[0]["body"] == {"host_ns": 100}


def test_vm_process_identity_uses_actual_descriptor_and_raw_argv(monkeypatch):
    import scripts.execution_capacity.reference_vm as module

    observed = {
        "pid": 14,
        "start_ticks": 81,
        "argv_digest": "raw-nul-hash",
        "executable_sha256": "a" * 64,
        "cgroup": "0::/original",
    }
    monkeypatch.setattr(module, "process_snapshot", lambda pid: dict(observed), raising=False)
    monkeypatch.setattr(module, "host_clock", lambda: {"boot_id": "boot"}, raising=False)
    assert module.process_identity(14) == {**observed, "boot_id": "boot"}


def test_vm_expected_argv_is_the_actual_nul_delimited_encoding():
    from hashlib import sha256

    from scripts.execution_capacity.reference_vm import argv_digest

    assert (
        argv_digest(["/usr/bin/qemu", "-uuid", "owned"])
        == sha256(b"/usr/bin/qemu\0-uuid\0owned\0").hexdigest()
    )
    assert argv_digest(["ab", "c"]) != argv_digest(["a", "bc"])
