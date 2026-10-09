"""Injected current-owned readbacks only; never starts Docker or a database."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from api.tests.scripts.test_protected_retention import evidence
from scripts.acceptance.collect_protected_retention import collect
from scripts.acceptance.protected_retention import validate_snapshot
from scripts.acceptance.protected_retention_probe import source_digest
from scripts.acceptance.strict_bridge import BridgeError


def setup(tmp_path, monkeypatch):
    value = evidence()
    binding = {
        "schema_version": 1,
        "run_id": "owned-test",
        "project": "owned-test",
        "invocation_id": "invocation",
        "kernel_container": "a" * 64,
        "kernel_image": "sha256:" + "b" * 64,
    }
    for name, key in [
        ("ACCEPTANCE_RUN_ID", "run_id"),
        ("ACCEPTANCE_PROJECT_ID", "project"),
        ("ACCEPTANCE_STRICT_INVOCATION_ID", "invocation_id"),
    ]:
        monkeypatch.setenv(name, binding[key])
    bootstrap = {
        "run_id": binding["run_id"],
        "project": binding["project"],
        "operator_id": value["owner_id"],
        "scope": {"type": "personal", "user_id": value["owner_id"], "team_id": None},
    }
    (tmp_path / "strict-binding.json").write_text(json.dumps(binding))
    (tmp_path / "strict-bootstrap.json").write_text(json.dumps(bootstrap))
    (tmp_path / "cleanup-journal" / "pending").mkdir(parents=True)
    action = {
        "run_id": binding["run_id"],
        "value": {
            "action": "delete-resource",
            "resource": "evaluation-batch",
            "resource_id": value["batch_id"],
            "expected_unknown_retention": True,
        },
    }
    (tmp_path / "cleanup-journal/pending/owned.json").write_text(json.dumps(action))
    record = {
        "scenario": "judge-timeout",
        "mode": "isolated",
        "batch": {"id": value["batch_id"]},
        "result": {"run_id": value["tables"]["attempts"][0]["run_id"]},
        "history": {
            "items": [
                {
                    "id": value["tables"]["scores"][0]["id"],
                    "judge_run_id": value["tables"]["intents"][0]["run_id"],
                    "score": {
                        "source": "model",
                        "status": "error",
                        "value": None,
                        "reason": "judge_execution_failed",
                    },
                }
            ]
        },
    }
    (tmp_path / "evaluation-lifecycle.json").write_text(json.dumps([record]))
    value["verification"] = validate_snapshot(
        value, batch_id=value["batch_id"], owner_id=value["owner_id"]
    )
    value["source_sha256"] = source_digest()
    document = {
        "Id": binding["kernel_container"],
        "Image": binding["kernel_image"],
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.project": binding["project"],
                "com.docker.compose.service": "opencitadel-execution-kernel",
                "com.opencitadel.acceptance.project": binding["project"],
                "com.opencitadel.acceptance.run": binding["run_id"],
            }
        },
    }
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "exec":
            assert argv == [
                "docker",
                "exec",
                "-i",
                binding["kernel_container"],
                "/app/.venv/bin/python",
                "/acceptance-driver/protected_retention_probe.py",
            ]
            assert json.loads(kwargs["input"])["batch_id"] == value["batch_id"]
        return SimpleNamespace(
            stdout=json.dumps([document] if argv[1] == "inspect" else value).encode()
        )

    return value, binding, document, execute, calls


def test_two_fresh_current_bound_readbacks_preserve_complete_private_evidence(
    tmp_path, monkeypatch
):
    value, binding, _, execute, calls = setup(tmp_path, monkeypatch)
    first = collect(tmp_path, value["batch_id"], "before", execute)
    final = collect(tmp_path, value["batch_id"], "after", execute)
    assert [call[1] for call in calls] == ["inspect", "exec", "inspect"] * 2
    assert first["binding"] == final["binding"] == binding
    assert final["status"] == "verified-protected-retention"
    assert final["verification"]["future_obligation"] == "open"
    assert final["evidence"] == {"before": value, "after": value}
    for phase in ("before", "after"):
        path = tmp_path / f"protected-retention-{value['batch_id']}.{phase}.raw.json"
        assert path.stat().st_mode & 0o777 == 0o600
        assert final[phase + "_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first["raw_sha256"] == final["before_sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "invocation",
        "no_flag",
        "workspace",
        "no_native",
        "different_scenario",
        "duplicate_native",
        "foreign_container",
        "stale_source",
        "changed_native_score",
        "active_send",
    ],
)
def test_current_collector_refuses_incomplete_foreign_or_unexpected_retention(
    tmp_path, monkeypatch, fault
):
    value, _, document, execute, _ = setup(tmp_path, monkeypatch)
    action_path = tmp_path / "cleanup-journal/pending/owned.json"
    action = json.loads(action_path.read_text())
    records_path = tmp_path / "evaluation-lifecycle.json"
    records = json.loads(records_path.read_text())
    if fault == "invocation":
        monkeypatch.setenv("ACCEPTANCE_STRICT_INVOCATION_ID", "foreign")
    elif fault == "no_flag":
        action["value"].pop("expected_unknown_retention")
    elif fault == "workspace":
        action["value"]["workspace_id"] = "another-workspace"
    elif fault == "no_native":
        records = []
    elif fault == "different_scenario":
        records[0]["scenario"] = "missing-usage"
    elif fault == "duplicate_native":
        records += deepcopy(records)
    elif fault == "foreign_container":
        document["Id"] = "foreign"
    elif fault == "stale_source":
        value["source_sha256"] = "0" * 64
    elif fault == "changed_native_score":
        records[0]["history"]["items"][0]["id"] = "foreign-score"
    elif fault == "active_send":
        value["tables"]["tasks"][0]["status"] = "call_started"
    action_path.write_text(json.dumps(action))
    records_path.write_text(json.dumps(records))
    with pytest.raises((ValueError, BridgeError)):
        collect(tmp_path, value["batch_id"], "before", execute)
    assert not [
        path
        for path in tmp_path.glob("protected-retention-*.json")
        if not path.name.endswith(".failed.json")
    ]


def test_after_rejects_changed_full_ledger_and_keeps_original_before(tmp_path, monkeypatch):
    value, _, _, execute, _ = setup(tmp_path, monkeypatch)
    collect(tmp_path, value["batch_id"], "before", execute)
    path = tmp_path / f"protected-retention-{value['batch_id']}.before.raw.json"
    original = path.read_bytes()
    value["tables"]["scores"][0]["evidence"].append({"new": "audit-mutation"})
    with pytest.raises(ValueError, match="source ledger/scoring/audit changed"):
        collect(tmp_path, value["batch_id"], "after", execute)
    assert path.read_bytes() == original
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.json").exists()
    diagnostic_path = (
        tmp_path / f"protected-retention-{value['batch_id']}.after.DIAGNOSTIC_ONLY.raw.json"
    )
    diagnostic = json.loads(diagnostic_path.read_text())
    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["retention_verified"] is False
    assert diagnostic["source"] == value
    assert diagnostic["source"]["tables"]["scores"][0]["evidence"][-1] == {"new": "audit-mutation"}
    assert diagnostic_path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.after.raw.json").exists()


def test_scheduler_clock_receipt_preserves_both_complete_raw_snapshots(tmp_path, monkeypatch):
    value, _, _, execute, _ = setup(tmp_path, monkeypatch)
    row = value["tables"]["judge_work"][0]
    row["checked_at"] = "2026-10-08T08:20:00+00:00"
    collect(tmp_path, value["batch_id"], "before", execute)
    row["checked_at"] = "2026-10-08T08:20:01+00:00"
    final = collect(tmp_path, value["batch_id"], "after", execute)
    assert (
        final["comparison_policy"] == "typed-source-equality-with-monotonic-judge-work-checked-at"
    )
    assert final["observed_clock_transitions"] == [
        {
            "table": "judge_work",
            "field": "checked_at",
            "intent_id": row["intent_id"],
            "scope_key": row["scope_key"],
            "before": "2026-10-08T08:20:00+00:00",
            "after": "2026-10-08T08:20:01+00:00",
        }
    ]
    assert (
        final["evidence"]["before"]["tables"]["judge_work"][0]["checked_at"]
        == "2026-10-08T08:20:00+00:00"
    )
    assert final["evidence"]["after"]["tables"]["judge_work"][0]["checked_at"] == row["checked_at"]


@pytest.mark.parametrize("fault", ["binding", "raw_tamper", "stale_before_source", "overwrite"])
def test_before_proof_is_immutable_and_cannot_be_replayed(tmp_path, monkeypatch, fault):
    value, _, _, execute, _ = setup(tmp_path, monkeypatch)
    collect(tmp_path, value["batch_id"], "before", execute)
    raw_path = tmp_path / f"protected-retention-{value['batch_id']}.before.raw.json"
    receipt_path = tmp_path / f"protected-retention-{value['batch_id']}.before.json"
    if fault == "binding":
        receipt = json.loads(receipt_path.read_text())
        receipt["binding"]["invocation_id"] = "old"
        receipt_path.write_text(json.dumps(receipt))
    elif fault == "raw_tamper":
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
    elif fault == "stale_before_source":
        raw = json.loads(raw_path.read_text())
        raw["source_sha256"] = "0" * 64
        raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=r"(stale|foreign|overwritten)"):
        collect(tmp_path, value["batch_id"], "before" if fault == "overwrite" else "after", execute)


def test_failed_probe_preserves_only_safe_private_diagnostic(tmp_path, monkeypatch):
    import subprocess

    value, _, _, original, _ = setup(tmp_path, monkeypatch)

    def failed(argv, **kwargs):
        if argv[1] == "exec":
            raise subprocess.CalledProcessError(
                1,
                argv,
                output=json.dumps(
                    {
                        "error": {
                            "stage": "source-table",
                            "table": "scores",
                            "exception": "ProgrammingError",
                            "sqlstate": "42703",
                        }
                    }
                ).encode(),
                stderr=b"private SQL parameters must never be copied",
            )
        return original(argv, **kwargs)

    with pytest.raises(subprocess.CalledProcessError):
        collect(tmp_path, value["batch_id"], "before", failed)
    path = tmp_path / f"protected-retention-{value['batch_id']}.before.failed.json"
    assert path.stat().st_mode & 0o777 == 0o600
    diagnostic = json.loads(path.read_text())
    assert diagnostic["status"] == "failed"
    assert diagnostic["error"] == {
        "stage": "source-table",
        "table": "scores",
        "exception": "ProgrammingError",
        "sqlstate": "42703",
    }
    assert "private SQL" not in path.read_text()
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.before.raw.json").exists()


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "stage": "source-table",
            "exception": "ProgrammingError",
            "reason": "private SQL parameters",
        },
        {
            "stage": "source-table",
            "exception": "ProgrammingError",
            "table": ["private table parameters"],
        },
        {"stage": "source-table", "exception": "ProgrammingError", "sqlstate": True},
    ],
)
def test_failed_probe_never_exports_untrusted_diagnostic_payload(tmp_path, monkeypatch, candidate):
    import subprocess

    value, _, _, original, _ = setup(tmp_path, monkeypatch)

    def failed(argv, **kwargs):
        if argv[1] == "exec":
            raise subprocess.CalledProcessError(
                1,
                argv,
                output=json.dumps({"error": candidate}).encode(),
                stderr=b"private SQL parameters",
            )
        return original(argv, **kwargs)

    with pytest.raises(subprocess.CalledProcessError):
        collect(tmp_path, value["batch_id"], "before", failed)
    path = tmp_path / f"protected-retention-{value['batch_id']}.before.failed.json"
    assert json.loads(path.read_text())["error"] == {
        "stage": "probe-execution",
        "exception": "CalledProcessError",
    }
    assert "private" not in path.read_text()


def test_host_validator_failure_is_preserved_without_failed_private_payload(tmp_path, monkeypatch):
    value, _, _, execute, _ = setup(tmp_path, monkeypatch)
    value["tables"]["tasks"][0]["status"] = "call_started"
    with pytest.raises(ValueError, match="active local send"):
        collect(tmp_path, value["batch_id"], "before", execute)
    path = tmp_path / f"protected-retention-{value['batch_id']}.before.failed.json"
    diagnostic = json.loads(path.read_text())
    assert diagnostic["error"] == {
        "stage": "host-validation",
        "exception": "ValueError",
        "reason": "active local send",
    }
    assert "tables" not in diagnostic
    assert path.stat().st_mode & 0o777 == 0o600


def test_native_mode_must_match_current_authoritative_namespace(tmp_path, monkeypatch):
    value, _, _, execute, _ = setup(tmp_path, monkeypatch)
    path = tmp_path / "evaluation-lifecycle.json"
    native = json.loads(path.read_text())
    native[0]["mode"] = "recorded"
    path.write_text(json.dumps(native))
    with pytest.raises(ValueError, match="native timeout mode differs"):
        collect(tmp_path, value["batch_id"], "before", execute)
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.before.raw.json").exists()


def test_failed_complete_source_is_private_diagnostic_only_never_verified_proof(
    tmp_path, monkeypatch
):
    import subprocess

    value, binding, _, original, _ = setup(tmp_path, monkeypatch)

    def failed(argv, **kwargs):
        if argv[1] == "exec":
            body = {
                "diagnostic_only": True,
                "retention_verified": False,
                "diagnostic_source": value,
                "error": {
                    "stage": "snapshot-validation",
                    "exception": "ValueError",
                    "reason": "expected unknown reservation missing",
                },
            }
            raise subprocess.CalledProcessError(1, argv, output=json.dumps(body).encode())
        return original(argv, **kwargs)

    with pytest.raises(subprocess.CalledProcessError):
        collect(tmp_path, value["batch_id"], "before", failed)
    path = tmp_path / f"protected-retention-{value['batch_id']}.before.DIAGNOSTIC_ONLY.raw.json"
    diagnostic = json.loads(path.read_text())
    assert diagnostic["binding"] == binding
    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["retention_verified"] is False
    assert diagnostic["source"] == value
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.before.raw.json").exists()
    assert not (tmp_path / f"protected-retention-{value['batch_id']}.before.json").exists()
