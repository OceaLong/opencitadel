"""Only injected subprocess executors; no Docker or resource runtime."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from api.tests.scripts.test_accounting_retention import evidence
from scripts.acceptance.accounting_probe import source_digest
from scripts.acceptance.accounting_retention import validate_accounting
from scripts.acceptance.collect_accounting import collect
from scripts.acceptance.strict_bridge import BridgeError


def setup(tmp_path, monkeypatch):
    value = evidence()
    binding = {
        "run_id": "owned-test",
        "project": "owned-test",
        "invocation_id": "invocation",
        "kernel_container": "a" * 64,
        "kernel_image": "sha256:" + "b" * 64,
    }
    for name, key in (
        ("ACCEPTANCE_RUN_ID", "run_id"),
        ("ACCEPTANCE_PROJECT_ID", "project"),
        ("ACCEPTANCE_STRICT_INVOCATION_ID", "invocation_id"),
    ):
        monkeypatch.setenv(name, binding[key])
    (tmp_path / "strict-binding.json").write_text(json.dumps(binding))
    (tmp_path / "strict-bootstrap.json").write_text(json.dumps({"operator_id": value["owner_id"]}))
    (tmp_path / "cleanup-journal" / "pending").mkdir(parents=True)
    (tmp_path / "cleanup-journal/pending/owned.json").write_text(
        json.dumps(
            {
                "run_id": binding["run_id"],
                "value": {
                    "action": "delete-resource",
                    "resource": "evaluation-batch",
                    "resource_id": value["batch_id"],
                    "retained_accounting": True,
                },
            }
        )
    )
    value["retention"] = validate_accounting(
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
    return value, binding, document


def test_fixed_read_only_probe_and_exact_bytes_are_retained(tmp_path, monkeypatch):
    value, binding, document = setup(tmp_path, monkeypatch)
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        assert kwargs["check"] is True
        if argv[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([document]).encode())
        assert argv == [
            "docker",
            "exec",
            "-i",
            binding["kernel_container"],
            "/app/.venv/bin/python",
            "/acceptance-driver/accounting_probe.py",
        ]
        assert json.loads(kwargs["input"])["batch_id"] == value["batch_id"]
        return SimpleNamespace(stdout=json.dumps(value).encode())

    result = collect(tmp_path, value["batch_id"], execute)
    assert [call[1] for call in calls] == ["inspect", "exec", "inspect"]
    raw = (tmp_path / f"evaluation-lifecycle-accounting-{value['batch_id']}.raw.json").read_bytes()
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["status"] == "retained-accounting"


@pytest.mark.parametrize(
    "fault", ["foreign_container", "stale_source", "foreign_owner", "active_slot"]
)
def test_collector_fails_before_writing_receipt_on_invalid_source(tmp_path, monkeypatch, fault):
    value, _, document = setup(tmp_path, monkeypatch)
    if fault == "foreign_container":
        document["Id"] = "foreign"
    if fault == "stale_source":
        value["source_sha256"] = "0" * 64
    if fault == "foreign_owner":
        value["owner_id"] = "foreign"
    if fault == "active_slot":
        value["buckets"][0]["slots"] = 1

    def execute(argv, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps([document] if argv[1] == "inspect" else value).encode()
        )

    with pytest.raises((ValueError, BridgeError)):
        collect(tmp_path, value["batch_id"], execute)
    assert not list(tmp_path.glob("evaluation-lifecycle-accounting-*.json"))
