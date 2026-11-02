"""Pure-file checks for failed native preparation without v3 close promotion."""

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest
from scripts.execution_capacity import (
    attempt,
    native_failed_transport,
    native_failure_transport,
    native_transport,
)
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.native_failed_transport import prepare_failed_files
from scripts.execution_capacity.native_failure_transport import (
    NativeFailureDrainWriter,
    reconcile_failure_snapshot_v2,
    reopen_failure_prefix,
)
from scripts.execution_capacity.native_raw import IDENTITY, _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter, reopen_native_commitment

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
    command = fixture["command"]
    raw = _canonical(command)
    key = hashlib.sha256(raw).hexdigest()
    return fixture, raw, key, {"native_commands": [key]}


def _snapshot(command, *, content=b"", acknowledged=0):
    artifacts = []
    if content:
        artifacts.append(
            {
                "artifact_id": "callback-id",
                "observed_bytes": len(content),
                "retained_bytes": len(content),
                "acknowledged_bytes": acknowledged,
                "sha256": hashlib.sha256(content).hexdigest(),
                "received_ns": "25",
                "source_record_refs": [],
            }
        )
    return _canonical(
        {
            **{name: command[name] for name in IDENTITY},
            "wire_version": 2,
            "observed_ns": "50",
            "failure_ns": "20",
            "retention_deadline_ns": "10000000020",
            "cause": "failure",
            "discarded_bytes": 0,
            "held_bytes": len(content) - acknowledged,
            "operations": [],
            "handles": [],
            "artifacts": artifacts,
            "disposition": "partial" if content != content[:acknowledged] else "settled",
        }
    )


def _callback(command, content):
    chunk = content[:49152]
    return {
        **{name: command[name] for name in IDENTITY},
        "wire_version": 2,
        "artifact_id": "callback-id",
        "observed_bytes": len(content),
        "retained_bytes": len(content),
        "retained_sha256": hashlib.sha256(content).hexdigest(),
        "artifact_received_ns": "25",
        "failure_ns": "20",
        "retention_deadline_ns": "10000000020",
        "offset": 0,
        "bytes": len(chunk),
        "sha256": hashlib.sha256(chunk).hexdigest(),
        "data": chunk,
        "source_record_ref": {"kind": "independent"},
    }


def test_prepare_zero_record_zero_callback_retains_producer_bytes_only(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        prepared = prepare_failed_files(native, drain, raw)
        assert (prepared.state, prepared.record_ack_count, prepared.failure_ack_count) == (
            "prepared-prefix-only",
            0,
            0,
        )
        assert prepared.snapshot_sha256 == hashlib.sha256(raw).hexdigest()
        assert (native.root / "failure.json").read_bytes() == raw
        assert (native.root / "failure-drain.ndjson").read_bytes() == b""
        assert list((native.root / "records").iterdir()) == []
        assert not (native.root / "manifest.json").exists()
        assert ledger.records("native-failure-close") == ()
        with pytest.raises(ValueError, match="closed or uncertain"):
            native.append_record(_canonical(fixture["records"][0]) + b"\n")
        with pytest.raises(ValueError, match="closed or uncertain"):
            drain.append_chunk(_callback(fixture["command"], b"data"))
    assert reconcile_failure_snapshot_v2(root, plan, key, raw)["evidence_state"] == (
        "complete-evidence"
    )


def test_prepare_partial_ack_keeps_exact_snapshot_and_physical_prefix(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    content = b"a" * 49152 + b"tail"
    raw = _snapshot(fixture["command"], content=content, acknowledged=49152)
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        native.append_record(_canonical(fixture["records"][0]) + b"\n")
        with NativeFailureDrainWriter.create(native, wire_version=2) as drain:
            drain.append_chunk(_callback(fixture["command"], content))
            prepared = prepare_failed_files(native, drain, raw)
            assert (prepared.record_ack_count, prepared.failure_ack_count) == (1, 1)
            assert (native.root / "failure.json").read_bytes() == raw
            assert not (native.root / "manifest.json").exists()
            assert ledger.records("native-failure-close") == ()
    assert reconcile_failure_snapshot_v2(root, plan, key, raw)["evidence_state"] == (
        "partial-evidence"
    )


@pytest.mark.parametrize("defect", ["wire1", "foreign", "noncanonical", "oversize"])
def test_prepare_rejects_invalid_producer_bytes_without_close(tmp_path, monkeypatch, defect):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    if defect == "wire1":
        row = json.loads(raw)
        row["wire_version"] = 1
        raw = _canonical(row)
    elif defect == "foreign":
        row = json.loads(raw)
        row["sample_id"] = "foreign"
        raw = _canonical(row)
    elif defect == "noncanonical":
        raw = raw + b" "
    else:
        raw = b"x" * (8 * 1024 * 1024 + 1)
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        with pytest.raises((ValueError, TypeError)):
            prepare_failed_files(native, drain, raw)
        assert native.poisoned
        assert drain.poisoned
        assert not (native.root / "failure.json").exists()
        assert ledger.records("native-failure-close") == ()


def test_prepare_rejects_wire1_or_wrong_owned_drain(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=1) as wire1,
    ):
        with pytest.raises(ValueError, match="same unpoisoned wire2"):
            prepare_failed_files(native, wire1, raw)
        assert not (native.root / "failure.json").exists()
        other = tmp_path / "other"
        with (
            AttemptLedger.create(other, plan) as other_ledger,
            NativeHostWriter.create(other_ledger, command) as other_native,
            NativeFailureDrainWriter.create(other_native, wire_version=2) as other_drain,
        ):
            with pytest.raises(ValueError, match="same unpoisoned wire2"):
                prepare_failed_files(native, other_drain, raw)
            assert not (other_native.root / "failure.json").exists()


def test_prepare_requires_original_ledger_lock_still_open(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger = AttemptLedger.create(root, plan)
    native = NativeHostWriter.create(ledger, command)
    drain = NativeFailureDrainWriter.create(native, wire_version=2)
    ledger.__exit__(None, None, None)
    try:
        with (
            AttemptLedger.open(root, plan),
            pytest.raises(ValueError, match="open owned native attempt ledger"),
        ):
            prepare_failed_files(native, drain, _snapshot(fixture["command"]))
        assert not (native.root / "failure.json").exists()
    finally:
        native.close()
        drain.close()


def test_prepare_refuses_duplicate_and_unacknowledged_drain_suffix(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        prepare_failed_files(native, drain, raw)
        with pytest.raises(ValueError, match="same unpoisoned wire2"):
            prepare_failed_files(native, drain, raw)
    other = tmp_path / "suffix"
    with (
        AttemptLedger.create(other, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        os.write(drain.fd, b"unACKed")
        with pytest.raises(ValueError, match="unacknowledged drain suffix"):
            prepare_failed_files(native, drain, raw)
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "failure.json").exists()


def test_prepare_rejects_changed_acked_failure_line(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    content = b"data"
    raw = _snapshot(fixture["command"], content=content, acknowledged=len(content))
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        drain.append_chunk(_callback(fixture["command"], content))
        with drain.path.open("r+b") as stream:
            stream.write(b"X")
            stream.flush()
            os.fsync(stream.fileno())
        with pytest.raises(ValueError, match="ACKed line changed"):
            prepare_failed_files(native, drain, raw)
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "failure.json").exists()


def test_prepare_rejects_unacknowledged_record_shard_suffix(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        native.append_record(_canonical(fixture["records"][0]) + b"\n")
        with NativeFailureDrainWriter.create(native, wire_version=2) as drain:
            os.write(native.fd, b"X")
            with pytest.raises(ValueError, match="unacknowledged shard suffix"):
                prepare_failed_files(native, drain, raw)
            assert ledger.records("native-failure-close") == ()
            assert not (native.root / "failure.json").exists()


@pytest.mark.parametrize("defect", ["short-write", "snapshot-parent-fsync"])
def test_prepare_write_fault_retains_unclosed_prefix(tmp_path, monkeypatch, defect):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        if defect == "short-write":

            def short_write(fd, value):
                os.write(fd, value[:1])
                raise OSError("injected short native snapshot write")

            monkeypatch.setattr(native_transport, "_write_exact", short_write)
        else:
            original = native_transport._sync_directory

            def fail_snapshot_parent(path):
                if path == native.root and (native.root / "failure.json").exists():
                    raise OSError("injected snapshot parent fsync")
                return original(path)

            monkeypatch.setattr(native_transport, "_sync_directory", fail_snapshot_parent)
        with pytest.raises(OSError, match="injected"):
            prepare_failed_files(native, drain, raw)
        assert native.poisoned
        assert drain.poisoned
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "manifest.json").exists()
        assert (native.root / "failure.json").exists()


def test_prepare_rejects_record_ack_after_failure_open(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        native.append_record(_canonical(fixture["records"][0]) + b"\n")
        with pytest.raises(ValueError, match="source ACK owner/order/clock"):
            prepare_failed_files(native, drain, raw)
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "failure.json").exists()


def test_prepare_serializes_inflight_record_and_callback_writers(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    errors = []
    started = [threading.Event(), threading.Event()]

    def record_worker(native):
        started[0].set()
        try:
            native.append_record(_canonical(fixture["records"][0]) + b"\n")
        except ValueError as error:
            errors.append(str(error))

    def callback_worker(drain):
        started[1].set()
        try:
            drain.append_chunk(_callback(fixture["command"], b"data"))
        except ValueError as error:
            errors.append(str(error))

    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        with ledger.thread_lock:
            workers = [
                threading.Thread(target=record_worker, args=(native,)),
                threading.Thread(target=callback_worker, args=(drain,)),
            ]
            for worker in workers:
                worker.start()
            assert all(event.wait(2) for event in started)
            prepared = prepare_failed_files(native, drain, raw)
            assert (prepared.record_ack_count, prepared.failure_ack_count) == (0, 0)
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()
        assert len(errors) == 2
        assert all("closed or uncertain" in error for error in errors)
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-failure-ack") == ()


def test_ledger_exit_waits_for_prepare_before_unlocking_attempt(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    raw = _snapshot(fixture["command"])
    ledger = AttemptLedger.create(root, plan)
    native = NativeHostWriter.create(ledger, command)
    drain = NativeFailureDrainWriter.create(native, wire_version=2)
    entered, release, exit_started, exit_done = (threading.Event() for _ in range(4))
    prepared, errors = [], []
    original = native_failed_transport._snapshot

    def blocked_snapshot(*args):
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test prepare barrier expired")
        return original(*args)

    def run_prepare():
        try:
            prepared.append(prepare_failed_files(native, drain, raw))
        except (ValueError, OSError) as error:
            errors.append(error)

    def run_exit():
        exit_started.set()
        ledger.__exit__(None, None, None)
        exit_done.set()

    monkeypatch.setattr(native_failed_transport, "_snapshot", blocked_snapshot)
    preparing = threading.Thread(target=run_prepare)
    exiting = threading.Thread(target=run_exit)
    try:
        preparing.start()
        assert entered.wait(2)
        exiting.start()
        assert exit_started.wait(2)
        assert not exit_done.wait(0.1)
        with pytest.raises(BlockingIOError):
            AttemptLedger.open(root, plan)
    finally:
        release.set()
        preparing.join(2)
        exiting.join(2)
        native.close()
        drain.close()
    assert not preparing.is_alive()
    assert not exiting.is_alive()
    assert not errors
    assert len(prepared) == 1
    assert prepared[0].state == "prepared-prefix-only"
    with AttemptLedger.open(root, plan) as reopened:
        assert reopened.records("native-failure-close") == ()
    assert reconcile_failure_snapshot_v2(root, plan, key, raw)["failure_ack_count"] == 0
    ledger.__exit__(None, None, None)  # repeated exit is harmless
    with pytest.raises(ValueError, match="attempt ledger closed"):
        ledger.records("native-command")
    with pytest.raises(ValueError, match="attempt journal uncertain"):
        ledger.append("native-failure-ack", {})


def test_record_close_cannot_reuse_fd_during_inflight_ack(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    line = _canonical(fixture["records"][0]) + b"\n"
    root = tmp_path / "attempt"
    entered, release, close_started, close_done = (threading.Event() for _ in range(4))
    errors, receipts = [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        original = native_transport._write_exact

        def blocked_write(fd, raw):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test record write barrier expired")
            return original(fd, raw)

        def run_append():
            try:
                receipts.append(native.append_record(line))
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_close():
            close_started.set()
            native.close()
            close_done.set()

        monkeypatch.setattr(native_transport, "_write_exact", blocked_write)
        appending = threading.Thread(target=run_append)
        closing = threading.Thread(target=run_close)
        foreign = tmp_path / "foreign-record.bin"
        foreign_fd = None
        try:
            appending.start()
            assert entered.wait(2)
            closing.start()
            assert close_started.wait(2)
            assert not close_done.wait(0.1)
            foreign_fd = os.open(foreign, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        finally:
            release.set()
            appending.join(2)
            closing.join(2)
            if foreign_fd is not None:
                os.close(foreign_fd)
        assert not appending.is_alive()
        assert not closing.is_alive()
        assert not errors
        assert receipts == [1]
        assert foreign.read_bytes() == b""
        assert (native.root / "records" / "000000.ndjson").read_bytes() == line
        assert len(ledger.records("native-record-ack")) == 1
        with pytest.raises(ValueError, match="closed or uncertain"):
            native.append_record(line)
        assert len(ledger.records("native-record-ack")) == 1


def test_manual_native_close_refuses_prepare_without_receipt(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        native.close()
        with pytest.raises(ValueError, match="same unpoisoned wire2"):
            prepare_failed_files(native, drain, _snapshot(fixture["command"]))
        with pytest.raises(ValueError, match="closed or uncertain"):
            native.append_record(_canonical(fixture["records"][0]) + b"\n")
        assert ledger.records("native-record-ack") == ()
        assert ledger.records("native-failure-close") == ()
        assert not (native.root / "failure.json").exists()


def test_failure_create_and_callback_ack_cannot_outlive_native_close(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    entered, release, close_started, close_done = (threading.Event() for _ in range(4))
    drains, errors = [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        original_private = native_failure_transport._private_directory

        def blocked_private(path):
            if path == native.root:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test failure-open barrier expired")
            return original_private(path)

        def run_create():
            try:
                drains.append(NativeFailureDrainWriter.create(native, wire_version=2))
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_close():
            close_started.set()
            native.close()
            close_done.set()

        monkeypatch.setattr(native_failure_transport, "_private_directory", blocked_private)
        creating = threading.Thread(target=run_create)
        closing = threading.Thread(target=run_close)
        try:
            creating.start()
            assert entered.wait(2)
            closing.start()
            assert close_started.wait(2)
            assert not close_done.wait(0.1)
        finally:
            release.set()
            creating.join(2)
            closing.join(2)
        assert not creating.is_alive()
        assert not closing.is_alive()
        assert not errors
        assert len(drains) == 1
        drain = drains[0]
        try:
            assert len(ledger.records("native-failure-open")) == 1
            assert native.finished
            with pytest.raises(ValueError, match="closed or uncertain"):
                drain.append_chunk(_callback(fixture["command"], b"data"))
            with pytest.raises(ValueError, match="owned unfinished native command"):
                NativeFailureDrainWriter.create(native, wire_version=2)
            assert ledger.records("native-failure-ack") == ()
            with pytest.raises(ValueError, match="same unpoisoned wire2"):
                prepare_failed_files(native, drain, _snapshot(fixture["command"]))
            assert ledger.records("native-failure-close") == ()
            assert not (native.root / "failure.json").exists()
        finally:
            drain.close()


def test_native_close_before_failure_create_has_no_open_or_ack(tmp_path, monkeypatch):
    _fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        native.close()
        with pytest.raises(ValueError, match="owned unfinished native command"):
            NativeFailureDrainWriter.create(native, wire_version=2)
        assert ledger.records("native-failure-open") == ()
        assert ledger.records("native-failure-ack") == ()
        assert not (native.root / "failure-drain.ndjson").exists()


def test_success_finish_wins_race_against_failure_create(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    entered, release, create_started, create_done = (threading.Event() for _ in range(4))
    finished, created, errors = [], [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for row in fixture["records"]:
            native.append_record(_canonical(row) + b"\n")
        original_seal = native._seal_shard

        def blocked_seal():
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test successful seal barrier expired")
            return original_seal()

        def run_finish():
            try:
                finished.append(native.finish_complete())
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_create():
            create_started.set()
            try:
                created.append(NativeFailureDrainWriter.create(native, wire_version=2))
            except ValueError as error:
                errors.append(error)
            finally:
                create_done.set()

        monkeypatch.setattr(native, "_seal_shard", blocked_seal)
        finishing = threading.Thread(target=run_finish)
        creating = threading.Thread(target=run_create)
        try:
            finishing.start()
            assert entered.wait(2)
            creating.start()
            assert create_started.wait(2)
            assert not create_done.wait(0.1)
        finally:
            release.set()
            finishing.join(2)
            creating.join(2)
        assert not finishing.is_alive()
        assert not creating.is_alive()
        assert len(finished) == 1
        assert created == []
        assert len(errors) == 1
        assert "owned unfinished native command" in str(errors[0])
        assert len(ledger.records("native-close")) == 1
        assert ledger.records("native-failure-open") == ()
        assert ledger.records("native-failure-ack") == ()
        assert not (native.root / "failure-drain.ndjson").exists()
    assert reopen_native_commitment(root, plan, key).manifest_sha256 == finished[0]


def test_failure_create_wins_race_against_success_finish(tmp_path, monkeypatch):
    fixture, command, _key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    entered, release, finish_started, finish_done = (threading.Event() for _ in range(4))
    created, finished, errors = [], [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for row in fixture["records"]:
            native.append_record(_canonical(row) + b"\n")
        original_private = native_failure_transport._private_directory

        def blocked_private(path):
            if path == native.root:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test failed open barrier expired")
            return original_private(path)

        def run_create():
            try:
                created.append(NativeFailureDrainWriter.create(native, wire_version=2))
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_finish():
            finish_started.set()
            try:
                finished.append(native.finish_complete())
            except ValueError as error:
                errors.append(error)
            finally:
                finish_done.set()

        monkeypatch.setattr(native_failure_transport, "_private_directory", blocked_private)
        creating = threading.Thread(target=run_create)
        finishing = threading.Thread(target=run_finish)
        try:
            creating.start()
            assert entered.wait(2)
            finishing.start()
            assert finish_started.wait(2)
            assert not finish_done.wait(0.1)
        finally:
            release.set()
            creating.join(2)
            finishing.join(2)
        assert not creating.is_alive()
        assert not finishing.is_alive()
        assert len(created) == 1
        assert finished == []
        assert len(errors) == 1
        assert "failure ownership forbids complete closure" in str(errors[0])
        drain = created[0]
        try:
            assert len(ledger.records("native-failure-open")) == 1
            assert ledger.records("native-close") == ()
            assert ledger.records("native-failure-ack") == ()
            assert not (native.root / "manifest.json").exists()
            with pytest.raises(ValueError, match="closed or uncertain"):
                drain.append_chunk(_callback(fixture["command"], b"data"))
        finally:
            drain.close()


def test_native_close_waits_for_success_finish_before_releasing_fd(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    entered, release, close_started, close_done = (threading.Event() for _ in range(4))
    finished, errors = [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
    ):
        for row in fixture["records"]:
            native.append_record(_canonical(row) + b"\n")
        original_artifacts = native._artifacts

        def blocked_artifacts():
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test success artifact barrier expired")
            return original_artifacts()

        def run_finish():
            try:
                finished.append(native.finish_complete())
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_close():
            close_started.set()
            native.close()
            close_done.set()

        monkeypatch.setattr(native, "_artifacts", blocked_artifacts)
        finishing = threading.Thread(target=run_finish)
        closing = threading.Thread(target=run_close)
        try:
            finishing.start()
            assert entered.wait(2)
            closing.start()
            assert close_started.wait(2)
            assert not close_done.wait(0.1)
        finally:
            release.set()
            finishing.join(2)
            closing.join(2)
        assert not finishing.is_alive()
        assert not closing.is_alive()
        assert not errors
        assert len(finished) == 1
        assert len(ledger.records("native-close")) == 1
        assert ledger.records("native-failure-open") == ()
    assert reopen_native_commitment(root, plan, key).manifest_sha256 == finished[0]


def test_closed_ledger_refuses_success_manifest_before_seal(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    ledger = AttemptLedger.create(root, plan)
    native = NativeHostWriter.create(ledger, command)
    for row in fixture["records"]:
        native.append_record(_canonical(row) + b"\n")
    ledger.__exit__(None, None, None)
    try:
        with pytest.raises(ValueError, match="complete native record closure absent"):
            native.finish_complete()
        assert not (native.root / "manifest.json").exists()
        with AttemptLedger.open(root, plan) as reopened:
            assert reopened.records("native-close") == ()
        with pytest.raises(ValueError, match="closed native host attempt absent"):
            reopen_native_commitment(root, plan, key)
    finally:
        native.close()


def test_failure_close_cannot_reuse_fd_during_inflight_ack(tmp_path, monkeypatch):
    fixture, command, key, plan = _setup(monkeypatch)
    root = tmp_path / "attempt"
    entered, release, close_started, close_done = (threading.Event() for _ in range(4))
    errors, receipts = [], []
    with (
        AttemptLedger.create(root, plan) as ledger,
        NativeHostWriter.create(ledger, command) as native,
        NativeFailureDrainWriter.create(native, wire_version=2) as drain,
    ):
        original = native_failure_transport._write_exact

        def blocked_write(fd, raw):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test failure write barrier expired")
            return original(fd, raw)

        def run_append():
            try:
                receipts.append(drain.append_chunk(_callback(fixture["command"], b"data")))
            except (ValueError, OSError) as error:
                errors.append(error)

        def run_close():
            close_started.set()
            drain.close()
            close_done.set()

        monkeypatch.setattr(native_failure_transport, "_write_exact", blocked_write)
        appending = threading.Thread(target=run_append)
        closing = threading.Thread(target=run_close)
        foreign = tmp_path / "foreign-failure.bin"
        foreign_fd = None
        try:
            appending.start()
            assert entered.wait(2)
            closing.start()
            assert close_started.wait(2)
            assert not close_done.wait(0.1)
            foreign_fd = os.open(foreign, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        finally:
            release.set()
            appending.join(2)
            closing.join(2)
            if foreign_fd is not None:
                os.close(foreign_fd)
        assert not appending.is_alive()
        assert not closing.is_alive()
        assert not errors
        assert len(receipts) == 1
        assert foreign.read_bytes() == b""
        assert len(ledger.records("native-failure-ack")) == 1
        with pytest.raises(ValueError, match="closed or uncertain"):
            drain.append_chunk(_callback(fixture["command"], b"data"))
        with pytest.raises(ValueError, match="same unpoisoned wire2"):
            prepare_failed_files(native, drain, _snapshot(fixture["command"]))
        assert not (native.root / "failure.json").exists()
        assert ledger.records("native-failure-close") == ()
    assert reopen_failure_prefix(root, plan, key)["callback-id"]["acknowledged_bytes"] == 4
