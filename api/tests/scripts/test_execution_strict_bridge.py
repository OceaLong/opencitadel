# ruff: noqa: SIM117
"""Independent tests. Synthetic evidence here never represents runtime AC proof."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/acceptance"))
from scripts.acceptance.strict_bridge import (
    BridgeError,
    assert_kernel_identity,
    driver_argv,
    quiesced,
    validate_evidence,
)


def binding():
    return {
        "schema_version": 1,
        "invocation_id": "00000000-0000-4000-8000-000000000001",
        "run_id": "acceptance-test",
        "project": "acceptance-test",
        "revision": "a" * 40,
        "dirty_tree_digest": "b" * 64,
        "kernel_image": "sha256:" + "c" * 64,
        "kernel_container": "d" * 64,
        "migration": "0022_execution_export_lifecycle",
        "inventory_sha256": "e" * 64,
        "budget_inventory_sha256": "f" * 64,
    }


def inspection():
    value = binding()
    return {
        "Id": value["kernel_container"],
        "Image": value["kernel_image"],
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.project": value["project"],
                "com.docker.compose.service": "opencitadel-execution-kernel",
                "com.opencitadel.acceptance.project": value["project"],
                "com.opencitadel.acceptance.run": value["run_id"],
            }
        },
    }


def test_argv_uses_pinned_image_readonly_mounts_and_no_shell(tmp_path):
    args = driver_argv(["docker", "compose"], tmp_path, tmp_path / "input.json", binding())
    assert {"--no-deps", "--rm", "-T"}.issubset(args)
    assert "/app/.venv/bin/python" in args
    assert not {"sh", "-c"}.intersection(args)
    assert f"{tmp_path.resolve()}:/acceptance-driver:ro" in args
    assert args[-3:] == (
        "/acceptance-driver/strict_driver/main.py",
        "--input",
        "/acceptance-input.json",
    )


@pytest.mark.parametrize("field", ["Id", "Image", "labels", "running"])
def test_identity_refuses_foreign_or_replaced_kernel(field):
    document = inspection()
    if field == "labels":
        document["Config"]["Labels"]["com.opencitadel.acceptance.run"] = "foreign"
    elif field == "running":
        document["State"]["Running"] = False
    else:
        document[field] = "foreign"
    with pytest.raises(BridgeError):
        assert_kernel_identity(document, binding(), running=True)


def test_finally_restores_after_driver_error():
    calls = []

    def run(args):
        calls.append(tuple(args))

    with pytest.raises(ValueError, match="driver failed"):
        with quiesced(
            binding(), run=run, inspect=lambda: inspection(), ready=lambda: calls.append("ready")
        ):
            raise ValueError("driver failed")
    assert calls == [
        ("docker", "stop", "--time", "45", "d" * 64),
        ("docker", "start", "d" * 64),
        "ready",
    ]


def test_stop_failure_still_restores_and_checks_ready():
    calls = []

    def run(args):
        calls.append(tuple(args))
        if args[1] == "stop":
            raise RuntimeError("stop unknown")

    with pytest.raises(RuntimeError, match="stop unknown"):
        with quiesced(
            binding(), run=run, inspect=lambda: inspection(), ready=lambda: calls.append("ready")
        ):
            pytest.fail("driver cannot run after ambiguous stop")
    assert calls[-1] == "ready"
    assert calls[-2][1] == "start"


def test_restore_failure_preserves_both_errors():
    def run(args):
        if args[1] == "start":
            raise RuntimeError("start failed")

    with pytest.raises(BaseExceptionGroup) as error:
        with quiesced(binding(), run=run, inspect=lambda: inspection(), ready=lambda: None):
            raise ValueError("driver failed")
    assert [str(item) for item in error.value.exceptions] == ["driver failed", "start failed"]


def test_foreign_identity_never_stopped():
    calls = []
    wrong = inspection()
    wrong["Image"] = "other"
    with pytest.raises(BridgeError):
        with quiesced(binding(), run=calls.append, inspect=lambda: wrong, ready=lambda: None):
            pytest.fail("foreign kernel entered")
    assert not calls


def test_incomplete_evidence_is_never_a_pass(tmp_path):
    report = {"binding": binding(), "scenarios": [], "status": "passed"}
    with pytest.raises(BridgeError):
        validate_evidence(report, binding(), {}, tmp_path)


def synthetic_report(tmp_path):
    """Validator contract only; every record is explicitly synthetic test input."""
    import hashlib
    import json

    from scripts.acceptance.strict_bridge import SCENARIOS

    bootstrap = {
        "scope": {"type": "personal", "user_id": "synthetic-validator-user", "team_id": None}
    }
    report = {
        "schema_version": 1,
        "binding": binding(),
        "status": "passed",
        "errors": [],
        "bootstrap_sha256": hashlib.sha256(
            json.dumps(bootstrap, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "scenarios": [],
    }
    for ac, identities in SCENARIOS.items():
        for identity in sorted(identities):
            report["scenarios"].append(
                {
                    "requirement": ac,
                    "id": identity,
                    "status": "passed",
                    "test_id": "synthetic.validator." + identity,
                    "resource_ids": {"run_id": "synthetic-validator-run"},
                    "assertions": [{"id": "synthetic-validator-assertion", "passed": True}],
                    "fault": {
                        "mechanism": "child_process_termination"
                        if identity == "worker_death"
                        else "synthetic_validator_input"
                    },
                    "before": {"synthetic": True},
                    "after": {"synthetic": True},
                    "cleanup": {"synthetic": True},
                    "artifact": "strict-raw.json",
                }
            )
    from tests.scripts.test_scheduler_budget_evidence import evidence as budget_evidence

    for item in report["scenarios"]:
        if item["id"] == "scheduler_budget_stop":
            item.update(budget_evidence())
    from scripts.acceptance.strict_legacy import LEGACY_REVISION

    legacy = {"historical_revision": LEGACY_REVISION, "main_database_distinct": True}
    dependencies = {}
    for name, key in (
        ("strict-legacy-source.tar", "source_archive_sha256"),
        ("strict-legacy-before.json", "before_sha256"),
        ("strict-legacy-after.json", "after_sha256"),
    ):
        path = tmp_path / name
        path.write_text("synthetic validator fixture")
        dependencies[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    for scenario in report["scenarios"]:
        if (
            scenario["requirement"] == "AC22"
            or scenario["id"] == "missing_history_available_segment"
        ):
            scenario["artifact"] = "strict-legacy-raw.json"
            scenario["database"] = legacy
            scenario["cleanup"] = {"state": "disposed"}
    report["artifacts"] = []
    for name in ("strict-raw.json", "strict-legacy-raw.json"):
        raw = tmp_path / name
        raw.write_text(
            json.dumps(
                {
                    "binding": binding(),
                    "legacy": legacy,
                    **dependencies,
                    "scenarios": [
                        scenario for scenario in report["scenarios"] if scenario["artifact"] == name
                    ],
                }
            )
        )
        report["artifacts"].append(
            {"path": raw.name, "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}
        )
    return report, bootstrap


def test_complete_synthetic_validator_contract(tmp_path):
    report, bootstrap = synthetic_report(tmp_path)
    receipt = validate_evidence(report, binding(), bootstrap, tmp_path)
    assert receipt["requirements"] == ["AC02", "AC05", "AC12", "AC13", "AC19", "AC22"]


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "missing",
        "duplicate",
        "failed",
        "skipped",
        "false_assertion",
        "raw_digest",
        "raw_path",
        "process_death",
        "scope",
    ],
)
def test_rejects_invalid_synthetic_evidence(tmp_path, mutation):
    report, bootstrap = synthetic_report(tmp_path)
    if mutation == "stale":
        report["binding"] = {**binding(), "invocation_id": "another"}
    elif mutation == "missing":
        report["scenarios"].pop()
    elif mutation == "duplicate":
        report["scenarios"].append(report["scenarios"][0])
    elif mutation in {"failed", "skipped"}:
        report["scenarios"][0]["status"] = mutation
    elif mutation == "false_assertion":
        report["scenarios"][0]["assertions"][0]["passed"] = False
    elif mutation == "raw_digest":
        report["artifacts"][0]["sha256"] = "0" * 64
    elif mutation == "raw_path":
        report["artifacts"][0]["path"] = "../strict-raw.json"
    elif mutation == "scope":
        bootstrap["scope"]["user_id"] = "foreign"
    else:
        next(item for item in report["scenarios"] if item["id"] == "worker_death")["fault"][
            "mechanism"
        ] = "boundary_exception"
    with pytest.raises(BridgeError):
        validate_evidence(report, binding(), bootstrap, tmp_path)


def test_readonly_exclusion_includes_foreign_terminal_cleanup_and_late_effect():
    from strict_driver.ownership import reject_foreign

    reject_foreign([("batch", "owned")], {("batch", "owned")})
    for kind in ("batch", "lease", "run"):
        with pytest.raises(RuntimeError, match="foreign eligible work"):
            reject_foreign([(kind, "foreign-terminal-or-pending")], {("batch", "owned")})


@pytest.mark.asyncio
async def test_reset_proxy_fails_only_exact_lease_once_and_delegates_real_receipt():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from strict_driver.environment import ResetFault

    identity = uuid4()
    actual = {"resources": [{"id": "synthetic-adapter-contract"}]}
    delegate = SimpleNamespace(
        reset=AsyncMock(return_value=actual), prepare=AsyncMock(return_value=actual)
    )
    fault = ResetFault(delegate, identity)
    assert await fault.prepare(None, None, None, None) is actual
    with pytest.raises(RuntimeError, match="controlled reset"):
        await fault.reset(SimpleNamespace(id=identity), None, None, None)
    assert await fault.reset(SimpleNamespace(id=identity), None, None, None) is actual
    assert await fault.reset(SimpleNamespace(id=uuid4()), None, None, None) is actual
    assert delegate.reset.await_count == 2


@pytest.mark.parametrize("kind", ["batch", "suite"])
def test_ownership_journal_is_durable_before_ack_and_only_archives_safe_parent(tmp_path, kind):
    import json

    from scripts.acceptance.strict_bridge import record_owned

    scope = {"type": "personal", "user_id": "operator", "team_id": None}
    (tmp_path / "strict-bootstrap.json").write_text(json.dumps({"scope": scope}))
    record_owned(
        tmp_path,
        binding(),
        {"kind": kind, "id": "00000000-0000-4000-8000-000000000009", "scope": scope},
    )
    assert len((tmp_path / "strict-owned.ndjson").read_text().splitlines()) == 1
    pending = list((tmp_path / "cleanup-journal/pending").glob("*.json"))
    if kind == "suite":
        assert len(pending) == 1
        entry = json.loads(pending[0].read_text())
        assert entry["value"]["resource"] == "evaluation-suite"
    else:
        assert pending == []
    with pytest.raises(BridgeError, match="foreign ownership"):
        record_owned(
            tmp_path,
            binding(),
            {"kind": "run", "id": "00000000-0000-4000-8000-000000000009", "scope": {}},
        )


def test_archive_rejects_traversal_before_extraction(tmp_path, monkeypatch):
    import io
    import tarfile
    from types import SimpleNamespace

    from scripts.acceptance.strict_legacy import archive_legacy

    def archive(args, **kwargs):
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as value:
            member = tarfile.TarInfo("api/../../escape")
            member.size = 1
            value.addfile(member, io.BytesIO(b"x"))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.acceptance.strict_legacy.subprocess.run", archive)
    with pytest.raises(BridgeError, match="unsafe"):
        archive_legacy(tmp_path, tmp_path)
    assert not (tmp_path / "escape").exists()


def test_private_env_refuses_overwrite_and_newlines(tmp_path):
    from scripts.acceptance.strict_legacy import write_private_env

    path = tmp_path / "private.env"
    write_private_env(path, {"FIXTURE_PASSWORD": "test-only"})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private_env(path, {"FIXTURE_PASSWORD": "replacement"})
    with pytest.raises(BridgeError):
        write_private_env(tmp_path / "bad", {"FIXTURE_PASSWORD": "bad\nvalue"})


def test_child_ack_waits_for_durable_ownership(tmp_path):
    import json
    import os

    from scripts.acceptance.strict_bridge import atomic_json, stream_driver

    scope = {"type": "personal", "user_id": "operator", "team_id": None}
    atomic_json(tmp_path / "strict-bootstrap.json", {"scope": scope})
    owned = {"kind": "run", "id": "00000000-0000-4000-8000-000000000002", "scope": scope}
    child = (
        "import json,sys; print(json.dumps({'kind':'owned','body':"
        + repr(owned)
        + "}),flush=True); ack=sys.stdin.readline().strip(); print(json.dumps({'kind':'report','body':{'ack':ack}}),flush=True)"
    )
    cleanup = []
    report, code = stream_driver(
        [sys.executable, "-c", child],
        cwd=tmp_path,
        environment=os.environ.copy(),
        evidence=tmp_path,
        binding=binding(),
        cleanup=lambda: cleanup.append(True),
        timeout=5,
    )
    assert code == 0
    assert report == {"ack": "owned-durable"}
    assert json.loads((tmp_path / "strict-owned.ndjson").read_text())["value"] == owned
    assert cleanup == [True]


def test_interrupted_child_preserves_ownership_and_runs_cleanup(tmp_path):
    import os

    from scripts.acceptance.strict_bridge import atomic_json, stream_driver

    atomic_json(tmp_path / "strict-bootstrap.json", {"scope": {}})
    owned = {"kind": "run", "id": "00000000-0000-4000-8000-000000000002", "scope": {}}
    child = (
        "import json,sys; print(json.dumps({'kind':'owned','body':"
        + repr(owned)
        + "}),flush=True); sys.stdin.readline()"
    )
    cleanup = []
    with pytest.raises(BridgeError, match="terminal"):
        stream_driver(
            [sys.executable, "-c", child],
            cwd=tmp_path,
            environment=os.environ.copy(),
            evidence=tmp_path,
            binding=binding(),
            cleanup=lambda: cleanup.append(True),
            timeout=5,
        )
    assert (tmp_path / "strict-owned.ndjson").is_file()
    assert cleanup == [True]


def test_child_timeout_runs_cleanup_without_inventing_report(tmp_path):
    import os

    from scripts.acceptance.strict_bridge import stream_driver

    cleanup = []
    with pytest.raises(BridgeError, match="timed out"):
        stream_driver(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            environment=os.environ.copy(),
            evidence=tmp_path,
            binding=binding(),
            cleanup=lambda: cleanup.append(True),
            timeout=0,
        )
    assert cleanup == [True]
    assert not (tmp_path / "strict-report.json").exists()
