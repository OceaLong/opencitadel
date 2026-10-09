"""Pure fixed-argv host command tests. Fake executor never launches Docker."""

import json
import stat
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.acceptance import physical_fault_host as host
from scripts.acceptance.strict_bridge import BridgeError


def test_host_busy_only_retries_acquisition_and_never_effect(tmp_path, monkeypatch):
    binding = {
        "run_id": "owned",
        "project": "owned",
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
    (tmp_path / "strict-binding.json").write_text(json.dumps(binding))
    (tmp_path / "strict-bootstrap.json").write_text(json.dumps({"operator_id": "owner"}))
    document = {
        "Id": binding["kernel_container"],
        "Image": binding["kernel_image"],
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "owned",
                "com.docker.compose.service": "opencitadel-execution-kernel",
                "com.opencitadel.acceptance.project": "owned",
                "com.opencitadel.acceptance.run": "owned",
            }
        },
    }
    calls = []
    fault = str(uuid4())
    replies = [{"busy": True}, {"fault_id": fault}]

    def execute(args, **kwargs):
        if args[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([document]).encode())
        calls.append(args)
        assert args == [
            "docker",
            "exec",
            "-i",
            "a" * 64,
            "/app/.venv/bin/python",
            "/acceptance-driver/physical_fault_command.py",
        ]
        assert json.loads(kwargs["input"])["action"] == "arm"
        return SimpleNamespace(stdout=json.dumps(replies.pop(0)).encode())

    monkeypatch.setattr(host.time, "sleep", lambda _: None)
    result = host.invoke(
        "arm",
        root=tmp_path,
        execution_run_id=str(uuid4()),
        activity_id=str(uuid4()),
        kind="shell_receipt_loss",
        execute=execute,
    )
    assert result["fault_id"] == fault
    assert len(calls) == 2
    document["Id"] = "foreign"
    with pytest.raises(BridgeError):
        host.invoke(
            "arm",
            root=tmp_path,
            execution_run_id=str(uuid4()),
            activity_id=str(uuid4()),
            kind="shell_receipt_loss",
            execute=execute,
        )
    document["Id"] = binding["kernel_container"]
    private = tmp_path / "physical-fault-arm-kernel-stderr.log"
    private.write_bytes(b"stale")
    private.chmod(0o644)
    diagnostic = b"physical fault action failed: FaultError:recorded_slot_ambiguous"

    def failed_execute(args, **kwargs):
        if args[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([document]).encode())
        assert args[3] == binding["kernel_container"]
        raise subprocess.CalledProcessError(1, args, stderr=diagnostic)

    with pytest.raises(subprocess.CalledProcessError):
        host.invoke(
            "arm",
            root=tmp_path,
            execution_run_id=str(uuid4()),
            activity_id=str(uuid4()),
            kind="recorded_object_missing",
            execute=failed_execute,
        )
    assert private.read_bytes() == diagnostic
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
