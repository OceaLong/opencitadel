"""Pure-file failed source replay with genuine parent/child round lineage."""

import base64
import hashlib
import json
import os
from uuid import uuid4

import pytest
from scripts.execution_capacity import attempt, native_failed_source
from scripts.execution_capacity.attempt import AttemptLedger, ReadOnlyAttemptLedger, digest, encode
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_failed_close import close_failed_host
from scripts.execution_capacity.native_failed_preflight import (
    FAILURE_BYTES,
    JOURNAL_BYTES,
    JOURNAL_ROWS,
    WORK_BYTES,
    FixtureDiagnosticBudget,
    RunnerFailedOriginMap,
)
from scripts.execution_capacity.native_failed_source import verify_failed_diagnostic_source
from scripts.execution_capacity.native_failed_transport import prepare_failed_files
from scripts.execution_capacity.native_failure_transport import NativeFailureDrainWriter
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.native_transport import NativeHostWriter
from scripts.execution_capacity.reference_round import reserve_round
from scripts.execution_capacity.test_native_failed_close import _rechain
from scripts.execution_capacity.test_native_failed_transport import _callback, _setup, _snapshot


def _budget():
    return EvidenceBudget(
        bytes_limit=WORK_BYTES,
        work_bytes_limit=WORK_BYTES,
        row_limit=JOURNAL_BYTES,
        rows_limit=2 * JOURNAL_ROWS + 2,
    )


def _round_fixture(tmp_path, monkeypatch, mode="zero", *, wrong_command_identity=False):
    fixture, _command_raw, _key, _plan = _setup(monkeypatch)
    rid = str(uuid4())
    command = {**fixture["command"], "attempt_id": "foreign" if wrong_command_identity else rid}
    command_raw = _canonical(command)
    key = hashlib.sha256(command_raw).hexdigest()
    parent_root = tmp_path / "parent"
    parent_plan = {
        "attempt_id": "parent",
        "protocol_id": "protocol",
        "samples": [{"sample_id": "sample", "physical_window_id": "window"}],
    }
    child_plan = {
        "attempt_id": rid,
        "protocol_id": "protocol",
        "samples": [{"sample_id": "sample", "physical_window_id": "window"}],
        "round": {
            "parent_attempt_id": "parent",
            "round_id": rid,
            "sample_id": "sample",
            "window_id": "window",
        },
        "native_commands": [key],
    }
    with AttemptLedger.create(parent_root, parent_plan) as parent:
        (parent_root / "rounds").mkdir(mode=0o700)
        child_root = parent_root / "rounds" / rid
        reserve_round(parent, child_plan, child_root, seal_digest="a" * 64)
        with AttemptLedger.create(child_root, child_plan) as child:
            child.bind_clock()
            with NativeHostWriter.create(child, command_raw) as native:
                source_ref = None
                if mode == "sourced":
                    content = b"source"
                    observation = {
                        "kind": "private-chunk",
                        "purpose": "rejected-capture",
                        "artifact_id": "record-source-id",
                        "chunk_index": 0,
                        "data": base64.b64encode(content).decode(),
                    }
                    record = {
                        **fixture["records"][0],
                        "attempt_id": rid,
                        "sequence": 1,
                        "received_ns": "21",
                        "observation": observation,
                    }
                    native.append_record(_canonical(record) + b"\n")
                    source_ref = {
                        "kind": "native-record",
                        "sequence": 1,
                        "purpose": "rejected-capture",
                        "artifact_id": "record-source-id",
                        "chunk_index": 0,
                        "artifact_offset": 0,
                        "bytes": len(content),
                    }
                elif mode == "partial":
                    content = b"a" * 49152 + b"tail"
                else:
                    content = b""
                acknowledged = (
                    len(content) if mode == "sourced" else 49152 if mode == "partial" else 0
                )
                snapshot_raw = _snapshot(command, content=content, acknowledged=acknowledged)
                if source_ref is not None:
                    snapshot = json.loads(snapshot_raw)
                    snapshot["artifacts"][0]["source_record_refs"] = [source_ref]
                    snapshot_raw = _canonical(snapshot)
                with NativeFailureDrainWriter.create(native, wire_version=2) as drain:
                    if acknowledged:
                        callback = _callback(command, content)
                        if source_ref is not None:
                            callback["source_record_ref"] = source_ref
                        drain.append_chunk(callback)
                    prepare_failed_files(native, drain, snapshot_raw)
    host = close_failed_host(child_root, child_plan, key)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    origins = RunnerFailedOriginMap(parent_root, parent_root, child_root, child_root)
    return origins, key, host, target, parent_plan, child_plan


def _verify(origins, key, host, target, *, fixture_budget=None, evidence_budget=None):
    return verify_failed_diagnostic_source(
        origins,
        key,
        fresh_host=host,
        target_parent=target,
        fixture_budget=FixtureDiagnosticBudget() if fixture_budget is None else fixture_budget,
        evidence_budget=_budget() if evidence_budget is None else evidence_budget,
    )


@pytest.mark.parametrize("mode", ["zero", "partial", "sourced"])
def test_failed_source_replays_real_original_round_without_copy(tmp_path, monkeypatch, mode):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(
        tmp_path, monkeypatch, mode
    )
    monkeypatch.setattr(
        AttemptLedger,
        "open",
        lambda *_: pytest.fail("read-only source replay opened mutable attempt ledger"),
    )
    result = _verify(origins, key, host, target)
    assert result.state == "diagnostic-source-verified"
    assert result.outcome == "failed"
    assert result.evidence_state == host.evidence_state
    assert result.origins == origins
    assert result.close_row_digest == host.close_row_digest
    assert result.manifest_sha256 == host.manifest_sha256
    assert result.failure_sha256 == host.failure_sha256
    assert result.parent_plan_sha256 != result.child_plan_sha256
    assert result.parent_ledger_sha256 != result.child_ledger_sha256
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("defect", ["host", "plan", "close", "origin", "round", "physical"])
def test_failed_source_refuses_forged_host_lineage_or_physical_bytes(tmp_path, monkeypatch, defect):
    origins, key, host, target, parent_plan, child_plan = _round_fixture(tmp_path, monkeypatch)
    if defect == "host":
        host = host.model_copy(update={"close_row_digest": "0" * 64})
    elif defect == "plan":
        (origins.child_retained / "plan.json").write_bytes(b"{}\n")
    elif defect == "close":

        def change(rows):
            close = next(row for row in rows if row["kind"] == "native-failure-close")
            close["body"]["failure_drain_bytes"] = 1

        _rechain(origins.child_retained, child_plan, change)
    elif defect == "origin":
        origins = RunnerFailedOriginMap(
            origins.parent_origin,
            origins.parent_retained,
            target / "wrong-original-child",
            origins.child_retained,
        )
    elif defect == "round":

        def change(rows):
            reservation = next(row for row in rows if row["kind"] == "round-reserved")
            reservation["body"]["child_path"] = str(target / "wrong-child")

        _rechain(origins.parent_retained, parent_plan, change)
    else:
        path = origins.child_retained / f"native-{key}" / "failure.json"
        raw = path.read_bytes()
        path.write_bytes(b"X" + raw[1:])
    with pytest.raises((ValueError, OSError, KeyError)):
        _verify(origins, key, host, target)


def test_failed_source_rejects_oversize_before_readonly_ledger_parse(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    with (origins.child_retained / "attempt.jsonl").open("ab") as stream:
        stream.write(b" " * (JOURNAL_BYTES + 1))
    monkeypatch.setattr(
        ReadOnlyAttemptLedger,
        "open",
        lambda *_args, **_kwargs: pytest.fail("unbounded ledger parsed before preflight"),
    )
    with pytest.raises(ValueError, match="fixture bound"):
        _verify(origins, key, host, target)


def test_failed_source_rejects_unbounded_evidence_budget_before_reservation(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    fixture_budget = FixtureDiagnosticBudget()
    with pytest.raises(ValueError, match="shared finite"):
        _verify(
            origins,
            key,
            host,
            target,
            fixture_budget=fixture_budget,
            evidence_budget=EvidenceBudget(bytes_limit=WORK_BYTES + 1),
        )
    assert fixture_budget.source_reserved == fixture_budget.target_reserved == 0


def test_failed_source_rejects_registered_command_from_wrong_attempt(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(
        tmp_path, monkeypatch, wrong_command_identity=True
    )
    with pytest.raises(ValueError, match="native command/round identity"):
        _verify(origins, key, host, target)


@pytest.mark.parametrize("member", ["journal", "failure"])
def test_failed_source_rechecks_fixture_bound_after_preflight(tmp_path, monkeypatch, member):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    original = native_failed_source.preflight_failed_diagnostic

    def mutate_after_preflight(*args, **kwargs):
        result = original(*args, **kwargs)
        if member == "journal":
            path = origins.child_retained / "attempt.jsonl"
            with path.open("ab") as stream:
                stream.write(b" " * (JOURNAL_BYTES + 1))
        else:
            path = origins.child_retained / f"native-{key}" / "failure.json"
            with path.open("ab") as stream:
                stream.write(b" " * (FAILURE_BYTES + 1))
        return result

    monkeypatch.setattr(native_failed_source, "preflight_failed_diagnostic", mutate_after_preflight)
    with pytest.raises(ValueError, match=r"explicit reader bound|fixed bound"):
        _verify(origins, key, host, target)


def test_failed_source_rejects_journal_growth_before_over_cap_frame_parse(tmp_path, monkeypatch):
    origins, key, host, target, _parent_plan, _child_plan = _round_fixture(tmp_path, monkeypatch)
    path = origins.child_retained / "attempt.jsonl"
    prior = json.loads(path.read_bytes().splitlines()[-1])
    previous = prior["digest"]
    sequence = prior["sequence"]
    appended = []
    for _ in range(2):
        sequence += 1
        row = {
            "sequence": sequence,
            "previous": previous,
            "kind": "padding",
            "body": {"bytes": "x" * 70000},
        }
        previous = digest(row)
        appended.append(encode({**row, "digest": previous}) + b"\n")
    assert len(appended[0]) < JOURNAL_BYTES
    assert path.stat().st_size + sum(map(len, appended)) > JOURNAL_BYTES
    captured = {"fd": None, "injected": False, "padding_parsed": 0}
    original_open_private = attempt._open_private
    original_fstat = os.fstat
    original_loads = json.loads

    def capture_journal_fd(member, flags):
        fd = original_open_private(member, flags)
        if member == path:
            captured["fd"] = fd
        return fd

    def grow_after_first_stat(fd):
        before = original_fstat(fd)
        if fd == captured["fd"] and not captured["injected"]:
            captured["injected"] = True
            with path.open("ab") as stream:
                stream.write(b"".join(appended))
        return before

    def observe_json(raw, *args, **kwargs):
        if isinstance(raw, bytes) and b'"kind":"padding"' in raw:
            captured["padding_parsed"] += 1
            if captured["padding_parsed"] > 1:
                pytest.fail("over-cap journal frame reached JSON parser")
        return original_loads(raw, *args, **kwargs)

    monkeypatch.setattr(attempt, "_open_private", capture_journal_fd)
    monkeypatch.setattr(attempt.os, "fstat", grow_after_first_stat)
    monkeypatch.setattr(attempt.json, "loads", observe_json)
    budget = _budget()
    with pytest.raises(ValueError, match="original journal exceeds explicit reader bound"):
        _verify(origins, key, host, target, evidence_budget=budget)
    assert captured["injected"]
    assert captured["padding_parsed"] == 1
    assert budget.bytes < sum(map(len, appended)) * 64
