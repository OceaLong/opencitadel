"""Pure-file one-shot private stage copy of failed diagnostic fixtures."""

import hashlib
import os
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest
from scripts.execution_capacity import native_failed_copy, native_failed_preflight, proof_copy
from scripts.execution_capacity.attempt import AttemptLedger, ReadOnlyAttemptLedger
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_failed_copy import copy_failed_diagnostic_stage
from scripts.execution_capacity.native_failed_preflight import (
    JOURNAL_BYTES,
    JOURNAL_ROWS,
    WORK_BYTES,
    FixtureDiagnosticBudget,
    RunnerFailedOriginMap,
)
from scripts.execution_capacity.native_failed_source import verify_failed_diagnostic_source
from scripts.execution_capacity.test_native_failed_source import _round_fixture


def _budget():
    return EvidenceBudget(
        bytes_limit=WORK_BYTES,
        work_bytes_limit=WORK_BYTES,
        row_limit=JOURNAL_BYTES,
        rows_limit=4 * JOURNAL_ROWS + 100,
    )


def _copy(origins, key, host, target, *, fixture_budget=None, evidence_budget=None, **kwargs):
    return copy_failed_diagnostic_stage(
        origins,
        key,
        fresh_host=host,
        target_parent=target,
        stage_name="failed-stage",
        fixture_budget=FixtureDiagnosticBudget() if fixture_budget is None else fixture_budget,
        evidence_budget=_budget() if evidence_budget is None else evidence_budget,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["zero", "partial", "sourced"])
def test_failed_copy_one_reservation_independent_replay_and_reopen(tmp_path, monkeypatch, mode):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(
        tmp_path, monkeypatch, mode
    )
    quota = FixtureDiagnosticBudget()
    budget = _budget()
    monkeypatch.setattr(
        AttemptLedger,
        "open",
        lambda *_args, **_kwargs: pytest.fail("copy opened mutable attempt ledger"),
    )
    receipt = _copy(origins, key, host, target, fixture_budget=quota, evidence_budget=budget)
    assert receipt.state == "diagnostic-copied-verified"
    assert receipt.outcome == "failed"
    assert receipt.evidence_state == host.evidence_state
    assert receipt.close_row_digest == host.close_row_digest
    assert receipt.stage == target / "failed-stage"
    assert quota.source_reserved == quota.target_reserved > 0
    assert quota.work_reserved >= budget.bytes
    records = receipt.stage / "child" / f"native-{key}" / "records"
    if mode == "zero":
        assert list(records.iterdir()) == []
    for label, origin, retained, expected_sha in (
        ("parent", origins.parent_origin, origins.parent_retained, receipt.parent_ledger_sha256),
        ("child", origins.child_origin, origins.child_retained, receipt.child_ledger_sha256),
    ):
        view = ReadOnlyAttemptLedger.open(
            receipt.stage / label,
            origin=origin,
            budget=_budget(),
            max_journal_bytes=JOURNAL_BYTES,
            max_journal_rows=JOURNAL_ROWS,
        )
        assert view.ledger_sha256 == expected_sha
        assert (
            hashlib.sha256((receipt.stage / label / "attempt.jsonl").read_bytes()).hexdigest()
            == hashlib.sha256((retained / "attempt.jsonl").read_bytes()).hexdigest()
        )
        for staged in (receipt.stage / label).rglob("*"):
            if staged.is_file():
                original = retained / staged.relative_to(receipt.stage / label)
                assert (
                    hashlib.sha256(staged.read_bytes()).digest()
                    == hashlib.sha256(original.read_bytes()).digest()
                )
    if mode == "zero":
        assert (
            receipt.stage / "child" / f"native-{key}" / "failure-drain.ndjson"
        ).read_bytes() == b""


def test_failed_copy_refuses_forged_claim_without_stage(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    source = verify_failed_diagnostic_source(
        origins,
        key,
        fresh_host=host,
        target_parent=target,
        fixture_budget=FixtureDiagnosticBudget(),
        evidence_budget=_budget(),
    )
    with pytest.raises(ValueError, match="stale or forged"):
        _copy(origins, key, host, target, claimed_source=replace(source, failure_sha256="0" * 64))
    assert not (target / "failed-stage").exists()


def test_failed_copy_refuses_late_source_mutation(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy._verify_failed_source_after_preflight
    calls = 0

    def mutate_after_target_replay(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            path = origins.child_retained / f"native-{key}" / "failure.json"
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
        return result

    monkeypatch.setattr(
        native_failed_copy, "_verify_failed_source_after_preflight", mutate_after_target_replay
    )
    with pytest.raises(ValueError, match=r"original bytes changed|source path changed"):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()


def test_failed_copy_rejects_late_source_fileset_suffix(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy._verify_failed_source_after_preflight
    calls = 0

    def add_after_target_replay(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            (origins.child_retained / f"native-{key}" / "foreign").write_bytes(b"x")
        return result

    monkeypatch.setattr(
        native_failed_copy, "_verify_failed_source_after_preflight", add_after_target_replay
    )
    with pytest.raises(ValueError, match="exact root fileset"):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()


def test_failed_copy_rejects_late_stage_mutation_after_target_replay(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy._verify_failed_source_after_preflight
    calls = 0

    def mutate_after_target_replay(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            path = target / "failed-stage" / "child" / f"native-{key}" / "failure.json"
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
        return result

    monkeypatch.setattr(
        native_failed_copy, "_verify_failed_source_after_preflight", mutate_after_target_replay
    )
    with pytest.raises(ValueError, match="original bytes changed"):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()


@pytest.mark.parametrize("defect", ["extra", "symlink", "suffix", "missing-drain"])
def test_failed_copy_refuses_corrupt_stage_prefix(tmp_path, monkeypatch, defect):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy.copy_private
    calls = 0

    def corrupt_stage(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            native = target / "failed-stage" / "child" / f"native-{key}"
            drain = native / "failure-drain.ndjson"
            if defect == "extra":
                (native / "extra").write_bytes(b"x")
            elif defect == "symlink":
                failure = native / "failure.json"
                failure.unlink()
                failure.symlink_to(native / "command.json")
            elif defect == "suffix":
                with drain.open("ab") as stream:
                    stream.write(b"suffix")
            else:
                drain.unlink()
        return result

    monkeypatch.setattr(native_failed_copy, "copy_private", corrupt_stage)
    with pytest.raises((ValueError, OSError)):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()


def test_failed_copy_refuses_target_exhaustion_and_duplicate_stage(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="quota exceeded"):
        _copy(origins, key, host, target, fixture_budget=FixtureDiagnosticBudget(target_limit=1))
    assert list(target.iterdir()) == []
    stage = target / "failed-stage"
    stage.mkdir(mode=0o700)
    marker = stage / "marker"
    marker.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        _copy(origins, key, host, target)
    assert marker.read_bytes() == b"keep"


def test_failed_copy_rechecks_target_space_after_one_reservation(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = os.fstatvfs
    calls = 0

    def exhausted_after_preflight(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd)
        return SimpleNamespace(f_bavail=0, f_frsize=1)

    monkeypatch.setattr(native_failed_preflight.os, "fstatvfs", exhausted_after_preflight)
    quota = FixtureDiagnosticBudget()
    with pytest.raises(ValueError, match="disk space unavailable"):
        _copy(origins, key, host, target, fixture_budget=quota)
    assert calls == 2
    assert quota.source_reserved == quota.target_reserved > 0
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("fault", ["short-write", "fsync"])
def test_failed_copy_fault_keeps_unusable_prefix(tmp_path, monkeypatch, fault):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    if fault == "short-write":
        monkeypatch.setattr(proof_copy.os, "write", lambda *_args: 0)
    else:
        original = os.fsync

        def fail_file_fsync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("injected fsync failure")
            return original(fd)

        monkeypatch.setattr(proof_copy.os, "fsync", fail_file_fsync)
    with pytest.raises(OSError, match=r"made no progress|injected fsync failure"):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()


def test_failed_copy_single_preflight_and_no_claimed_shortcut(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy.preflight_failed_diagnostic
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(native_failed_copy, "preflight_failed_diagnostic", counted)
    _copy(origins, key, host, target)
    assert calls == 1


def test_failed_copy_stage_cannot_pass_public_source_ancestry_gate(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    receipt = _copy(origins, key, host, target)
    staged_origins = RunnerFailedOriginMap(
        origins.parent_origin,
        receipt.stage / "parent",
        origins.child_origin,
        receipt.stage / "child",
    )
    with pytest.raises(ValueError, match="ancestry overlaps"):
        verify_failed_diagnostic_source(
            staged_origins,
            key,
            fresh_host=host,
            target_parent=target,
            fixture_budget=FixtureDiagnosticBudget(),
            evidence_budget=_budget(),
        )


def test_failed_copy_refuses_exhausted_shared_evidence_before_stage(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    budget = EvidenceBudget(
        bytes_limit=1,
        work_bytes_limit=1,
        row_limit=JOURNAL_BYTES,
        rows_limit=4 * JOURNAL_ROWS + 100,
    )
    with pytest.raises(ValueError, match="quota exceeded"):
        _copy(origins, key, host, target, evidence_budget=budget)
    assert list(target.iterdir()) == []


def test_failed_copy_independent_target_replay_rejects_same_size_mutation(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_copy._stage_probe
    calls = 0

    def mutate_after_probe(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            path = target / "failed-stage" / "child" / f"native-{key}" / "failure.json"
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
        return result

    monkeypatch.setattr(native_failed_copy, "_stage_probe", mutate_after_probe)
    with pytest.raises(ValueError, match=r"Expecting value|failed native|snapshot|differs"):
        _copy(origins, key, host, target)
    assert (target / "failed-stage").is_dir()
