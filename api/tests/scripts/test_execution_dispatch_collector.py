"""Pure host orchestration checks with synthetic metadata; no Docker subprocess."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from scripts.acceptance.collect_dispatch_audit import collect
from scripts.acceptance.dispatch_audit import AuditLog, observe
from scripts.acceptance.strict_bridge import BridgeError


def fixture(tmp_path, monkeypatch):
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
    (tmp_path / "strict-bootstrap.json").write_text(json.dumps({"operator_id": "owner"}))
    from scripts.acceptance.physical_faults import source_digest

    digest = source_digest()
    log = AuditLog(
        tmp_path / "synthetic.ndjson",
        {"run_id": binding["run_id"], "project": binding["project"], "source_sha256": digest},
    )
    ids = [f"00000000-0000-4000-8000-{index:012d}" for index in (1, 2, 3)]

    async def populate():
        async def noop(*args, **kwargs):
            return None

        for identity, kind in zip(ids, ("replay", "catalog", "catalog"), strict=True):
            ctx = SimpleNamespace(
                run=SimpleNamespace(run_id=identity),
                activity_id="activity",
                generation=0,
                claim_generation=1,
                owner_user_id="owner",
                team_id=None,
            )

            async def body(_self, _payload, context, boundary=kind):
                return await observe(noop, boundary, log)(None, None, context)

            await observe(body, "handler", log)(None, None, ctx)

    asyncio.run(populate())
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
    return binding, ids, document, log


def test_collector_reads_only_exact_inspected_kernel_and_binds_raw_bytes(tmp_path, monkeypatch):
    binding, ids, document, log = fixture(tmp_path, monkeypatch)
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 30
        raw = json.dumps([document]).encode() if argv[1] == "inspect" else log.path.read_bytes()
        return SimpleNamespace(stdout=raw)

    result = collect(
        root=tmp_path, replay_run=ids[0], source_run=ids[1], isolated_run=ids[2], execute=execute
    )
    assert [call[1] for call in calls] == ["inspect", "exec", "inspect"]
    assert all(call[2] == binding["kernel_container"] for call in calls)
    assert result["observation"]["catalog_entries"] == 0
    assert (tmp_path / f"dispatch-{ids[0]}.json").is_file()
    assert result["raw_sha256"] == hashlib.sha256(log.path.read_bytes()).hexdigest()


def test_foreign_container_is_refused_before_log_read(tmp_path, monkeypatch):
    _, ids, document, _ = fixture(tmp_path, monkeypatch)
    document["Id"] = "foreign"
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=json.dumps([document]).encode())

    with pytest.raises(BridgeError, match="replaced"):
        collect(
            root=tmp_path,
            replay_run=ids[0],
            source_run=ids[1],
            isolated_run=ids[2],
            execute=execute,
        )
    assert len(calls) == 1
    assert not (tmp_path / f"dispatch-{ids[0]}.json").exists()


def test_actual_restart_log_missing_positive_controls_is_saved_only_as_private_failure(
    tmp_path, monkeypatch
):
    binding, ids, document, log = fixture(tmp_path, monkeypatch)
    raw = (json.dumps(log.records()[0]) + "\n").encode()

    def execute(argv, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps([document]).encode() if argv[1] == "inspect" else raw
        )

    with pytest.raises(ValueError, match="positive control absent"):
        collect(
            root=tmp_path,
            replay_run=ids[0],
            source_run=ids[1],
            isolated_run=ids[2],
            execute=execute,
        )
    prefix = f"dispatch-{ids[0]}"
    path = tmp_path / f"{prefix}.DIAGNOSTIC_ONLY.ndjson"
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o777 == 0o600
    receipt = json.loads((tmp_path / f"{prefix}.failed.json").read_text())
    assert receipt["binding"] == binding
    assert receipt["diagnostic_only"] is True
    assert receipt["dispatch_verified"] is False
    assert receipt["error"] == {
        "stage": "audit-validation",
        "exception": "ValueError",
        "reason": "source/isolated positive control absent",
    }
    assert not (tmp_path / f"{prefix}.ndjson").exists()
    assert not (tmp_path / f"{prefix}.json").exists()


@pytest.mark.parametrize("field", ["project", "source_sha256"])
def test_foreign_observer_boot_never_exports_private_audit_bytes(tmp_path, monkeypatch, field):
    _, ids, document, log = fixture(tmp_path, monkeypatch)
    records = log.records()
    records[0][field] = "foreign"
    raw = b"\n".join(json.dumps(row).encode() for row in records) + b"\n"

    def execute(argv, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps([document]).encode() if argv[1] == "inspect" else raw
        )

    with pytest.raises(ValueError, match="foreign"):
        collect(
            root=tmp_path,
            replay_run=ids[0],
            source_run=ids[1],
            isolated_run=ids[2],
            execute=execute,
        )
    assert not (tmp_path / f"dispatch-{ids[0]}.DIAGNOSTIC_ONLY.ndjson").exists()
    assert not (tmp_path / f"dispatch-{ids[0]}.json").exists()
