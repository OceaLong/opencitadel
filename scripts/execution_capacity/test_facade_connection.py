"""Facade CONNECTION evidence only, never standard-population acceptance.

Two explicit downstream test-local substitutions: capacity.derive_summary and
capacity.budget_errors. Fixed Summary is solely a call-order fixture. All safe
artifact IO and original context/replay/copy/destination gates execute unchanged.
"""

import asyncio
import hashlib
import json
import shutil

import pytest
from scripts.acceptance import capacity
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.offline_context import BaseLocation, OfflineProofContext
from scripts.execution_capacity.test_final_connection_fixture import acquire_final, seal_acquired

D = "a" * 64


def package(tmp_path, monkeypatch):
    (tmp_path / "guest").mkdir(mode=0o700)
    index_bytes = 4 * 1024 * 1024
    value = asyncio.run(
        acquire_final(
            tmp_path,
            monkeypatch,
            original_root=tmp_path / "guest" / "c2c-originals",
            index_bytes=index_bytes,
        )
    )
    base = seal_acquired(tmp_path, monkeypatch, value)
    context = OfflineProofContext(
        bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
        rounds=[],
        signing_secret=value.secret,
        cursor_secret=value.cursor,
        budget=value.budget,
        index_bytes=index_bytes,
    )
    projection, seal, rounds, origins, queries = context._checked()
    assert not rounds
    assert not origins
    assert not queries
    common = {"schema_version": 3, "attempt_id": seal.attempt_id, "protocol_id": seal.protocol_id}
    roles = {}

    def role(name, **fields):
        roles[name] = dict(**common, role=name, **fields)
        return roles[name]

    fixture = {
        "schema_version": 1,
        "fixture_id": "connection-only",
        "seed": 1,
        "scope_ids": ["fixture"],
        "counts": dict(capacity.FIXTURE_COUNTS),
        "window_start": "2026-06-01T00:00:00Z",
        "window_end": "2026-09-01T00:00:00Z",
        "status": "planned",
        "target": {
            "environment": "test",
            "runtime_id": "fixture",
            "database_id": "fixture",
            "scope_id": "fixture",
        },
        "ownership_journal": "ownership.jsonl",
    }
    roles["fixture"] = fixture
    binding = {
        "revision": "fixture",
        "dirty_tree_digest": D,
        "images": {"fixture": "sha256:" + D},
        "migration": "fixture",
        "fixture_manifest_digest": hashlib.sha256(json.dumps(fixture).encode()).hexdigest(),
    }
    role(
        "protocol",
        binding_digest=canonical_digest(binding),
        seal_id=seal.seal_id,
        registered_ns=1,
        clock_id="fixture",
        clock_unit="nanoseconds",
        latency_method="coordinator_predispatch_to_paint_upper_bound",
        rate_rule="each-1s-bin-at-least-2-v1",
        startup_ns=1,
        marker_cadence_ns=1,
        marker_timeout_ns=1,
        marker_count_bound=1,
        marker_max_outstanding=1,
        load_ready_ns=2000000000,
        warm_plan="same-target-explicit-prewarm-before-window",
        diagnostics_timing="after-timed-read-or-separate-clone",
        backend="linux-x86_64-kvm-qemu",
        network_backend="owned-netns-veth-qemu-usernet",
        samples=[],
        windows=[],
    )
    role(
        "measurements",
        markers=[],
        live_paints=[],
        samples=[],
        sources=[],
        browsers=[],
        resources=[],
        errors=[],
    )
    role("workload", windows=[], progress=[], source_acks=[], errors=[])
    roles["seal"] = seal.model_dump(mode="json")
    roles["c2c"] = projection.model_dump(mode="json")
    limits = dict.fromkeys(
        (
            "workers",
            "per_worker",
            "claim_batch_size",
            "pool_size",
            "max_overflow",
            "subject_concurrency",
            "judge_concurrency",
            "environment_concurrency",
            "physical_global",
            "physical_user",
            "physical_provider",
        ),
        1,
    )
    limits["policy_id"] = "fixture"
    environment = role(
        "environment",
        server_host_id="fixture",
        client_host_id="fixture",
        server_cpu_ids=[0],
        client_cpu_ids=[1],
        host_memory_bytes=1,
        server_memory_bytes=1,
        client_memory_bytes=1,
        overhead_memory_bytes=1,
        postgres_memory_max_bytes=1,
        cgroup_id="fixture",
        disk_id="fixture",
        disk_rotational=False,
        disk_direct_io=True,
        architecture="x86_64",
        os="fixture",
        browser="fixture",
        postgres="fixture",
        python="fixture",
        playwright="fixture",
        qemu_binary_sha256=D,
        viewport=[1440, 900],
        runtime_limits=limits,
    )
    role("resets", resets=[])
    link = {
        "namespace_inode": 1,
        "ifindex": 1,
        "peer_ifindex": 2,
        "address": "fixture",
        "route_destination": "fixture",
        "route_ifindex": 1,
        "qdisc_kind": "netem",
        "delay_us": 25000,
        "rate_bps": 20000000,
        "default_route": False,
    }
    role(
        "network",
        backend="owned-netns-veth-qemu-usernet",
        host_link=link,
        client_link=link,
        forward_bound_address="fixture",
        guest_ports=[1],
        global_ip_forward_changed=False,
        nat_changed=False,
        cdp_delay_ms=0,
        context_ids=[],
        validity={
            "added_rtt_min_ns": 1,
            "added_rtt_max_ns": 2,
            "throughput_min_bps": 1,
            "throughput_max_bps": 2,
        },
        calibrations=[],
        phase_intervals=[],
    )
    role("diagnostics", queries=[])
    counts = {
        name: sum(getattr(row.source, name) for row in seal.cohorts)
        for name in ("runs", "formal_events", "observations", "visible_steps")
    }
    role(
        "cleanup",
        rounds=[],
        cohorts=[row.model_dump(mode="json") for row in seal.cohorts],
        total=counts,
        dispositions=[],
        pending=[],
        quarantined=[],
        status="retained_immutable",
    )
    summary = {
        "environment": environment,
        "workload": {
            "windows": 0,
            "browsers_per_window": 0,
            "active_runs_per_window": 0,
            "effective_progress_records": 0,
            "rate_rule": "each-1s-bin-at-least-2-v1",
        },
        "cache": {
            "independent_cold_resets": 0,
            "warm_prewarms": 0,
            "backend": "linux-x86_64-kvm-qemu",
        },
        "cleanup": {
            "status": "retained_immutable",
            "pending": [],
            "quarantined": [],
            "retained_scopes": {"count": 0, "ordered_sha256": canonical_digest([])},
            "retained_formal_events": counts["formal_events"],
            "retained_observations": counts["observations"],
            "cohorts": {"count": 0, "ordered_sha256": canonical_digest([])},
            "total": counts,
        },
        "warm": {"first_screen": [1]},
        "cold": {"first_screen": [1]},
        "latency": {"fixture": [1]},
        "heap": {"peak_mib": 1, "visible_dom_rows": 1},
        "frames": {
            "count": 1,
            "p50_ms": 1,
            "p95_ms": 1,
            "max_ms": 1,
            "ordered_sha256": canonical_digest([1]),
            "long_tasks": {
                "count": 0,
                "over_200ms_count": 0,
                "max_ms": None,
                "ordered_sha256": canonical_digest([]),
            },
        },
        "step_capacity": {
            "cohort_id": "fixture",
            "scope_id": "fixture",
            "run_id": "fixture",
            "seal_id": seal.seal_id,
            "parity_digest": D,
            "visible_steps": 0,
            "formal_events": 0,
            "observations": 0,
            "warm": {"first_screen": [1]},
            "cold": {"first_screen": [1]},
        },
        "errors": [],
    }
    root = tmp_path / "safe"
    root.mkdir()
    artifacts = []
    for name, raw in roles.items():
        data = json.dumps(raw).encode()
        path = root / (name + ".json")
        path.write_bytes(data)
        artifacts.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "role": name,
                "schema_version": raw["schema_version"],
            }
        )
    return value, base, context, root, artifacts, binding, summary


def derive(context, root, artifacts, binding):
    return capacity.derive_capacity_report(
        artifacts=artifacts,
        binding=binding,
        completed_binding=binding,
        root=root,
        started_at="2026-09-21T00:00:00Z",
        finished_at="2026-09-21T00:01:00Z",
        proof_context=context,
    )


def test_facade_connection_real_original_copy_and_fresh_destination(tmp_path, monkeypatch):
    value, base, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    calls = []

    def downstream(roles, actual_binding):
        assert actual_binding == binding
        calls.append("capacity-after-original-replay")
        return summary

    monkeypatch.setattr(capacity, "derive_summary", downstream)
    monkeypatch.setattr(capacity, "budget_errors", lambda derived: [])
    report = derive(context, root, artifacts, binding)
    assert not capacity.validate_capacity_report(report, binding, root, proof_context=context)
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report))
    destination = tmp_path / "retained"
    destination.mkdir(mode=0o700)
    receipt = capacity.prepare_capacity_evidence(
        report_path=report_path,
        fixture_path=root / "fixture.json",
        evidence_root=destination,
        build={k: v for k, v in binding.items() if k != "fixture_manifest_digest"},
        run_id="fixture",
        project="fixture",
        proof_context=context,
    )
    assert receipt["errors"] == []
    assert "percentiles_ms" in receipt  # Connection-only substituted capacity output.
    assert len(calls) == 4
    assert (
        destination / "capacity-private/originals/unit-000000/c2c-originals/manifest.json"
    ).is_file()
    public = (destination / "capacity-validation.json").read_text()
    for private in (str(tmp_path), value.secret, value.cursor.decode(), "original private case"):
        assert private not in public
    # Report commitments are not authority: independently derived complete fields
    # reject hash-only forgery and empty numeric population substitutions.
    for frames in (
        {**report["frames"], "ordered_sha256": "f" * 64},
        {
            **report["frames"],
            "count": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "ordered_sha256": canonical_digest([]),
        },
    ):
        forged = {**report, "frames": frames}
        assert capacity.validate_capacity_report(forged, binding, root, proof_context=context)
    assert len(calls) == 6
    # Every operation reopens current bytes; no cached successful context authority.
    base.image_path.write_bytes(b"changed-after-copy")
    assert capacity.validate_capacity_report(report, binding, root, proof_context=context) == [
        "private C2c original replay failed"
    ]
    assert len(calls) == 6


def test_actual_private_stream_shards_package_and_fresh_copy(tmp_path, monkeypatch):
    from api.tests.scripts.capacity_population_private import OriginalProjectionShards
    from scripts.acceptance.capacity_io import load_artifacts
    from scripts.acceptance.capacity_models import Artifact
    from scripts.acceptance.capacity_package import PackageSession

    _, _, context, original, artifacts, binding, summary = package(tmp_path, monkeypatch)
    root = tmp_path / "streamed-package"
    root.mkdir(mode=0o700)
    replaced = {"seal", "c2c", "diagnostics", "cleanup"}
    retained = [row for row in artifacts if row["role"] not in replaced]
    for row in retained:
        shutil.copy2(original / row["path"], root / row["path"])
    sink = OriginalProjectionShards(root)
    try:
        context.stream_projection(sink)
        artifacts = [*retained, *sink.descriptors]
        with PackageSession(
            [Artifact.model_validate(row) for row in artifacts],
            root,
            resources=context.public_resources(),
        ) as session:
            roles = session.roles
            context.validate(roles, package=session)
            legacy, _ = load_artifacts([Artifact.model_validate(row) for row in artifacts], root)
            assert roles["cleanup"].total == legacy["cleanup"].total
            assert list(roles["cleanup"].cohorts) == legacy["cleanup"].cohorts
        monkeypatch.setattr(capacity, "derive_summary", lambda roles, actual_binding: summary)
        monkeypatch.setattr(capacity, "budget_errors", lambda derived: [])
        report = derive(context, root, artifacts, binding)
        assert capacity.validate_capacity_report(report, binding, root, proof_context=context) == []
        (root / "report.json").write_text(json.dumps(report))
        destination = tmp_path / "streamed-copy"
        destination.mkdir(mode=0o700)
        receipt = capacity.prepare_capacity_evidence(
            report_path=root / "report.json",
            fixture_path=root / "fixture.json",
            evidence_root=destination,
            build={k: v for k, v in binding.items() if k != "fixture_manifest_digest"},
            run_id="fixture",
            project="fixture",
            proof_context=context,
        )
        assert receipt["errors"] == []
        assert (
            destination / "capacity-private/originals/unit-000000/c2c-originals/manifest.json"
        ).is_file()
    finally:
        sink.close()


def test_real_connection_single_shard_cow_preserves_schema_error_and_receipt(tmp_path, monkeypatch):
    from api.tests.scripts.capacity_population_mutation import mutate_one_shard

    _, _, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    root.chmod(0o700)
    monkeypatch.setattr(capacity, "derive_summary", lambda roles, actual_binding: summary)
    monkeypatch.setattr(capacity, "budget_errors", lambda derived: [])
    valid = derive(context, root, artifacts, binding)
    assert capacity.validate_capacity_report(valid, binding, root, proof_context=context) == []
    original_hash = hashlib.sha256((root / "protocol.json").read_bytes()).hexdigest()
    changed_root = tmp_path / "cow-package"
    changed_root.mkdir(mode=0o700)

    def schema_fault(document):
        document["schema_version"] = True

    altered = mutate_one_shard(
        root,
        changed_root,
        artifacts,
        role="protocol",
        shard_index=0,
        mutate=schema_fault,
    )
    report = {**valid, "artifacts": altered}
    report["protocol_digest"] = next(row["sha256"] for row in altered if row["role"] == "protocol")
    errors = capacity.validate_capacity_report(report, binding, changed_root, proof_context=context)
    assert any("schema" in error for error in errors)
    assert hashlib.sha256((root / "protocol.json").read_bytes()).hexdigest() == original_hash
    (changed_root / "report.json").write_text(json.dumps(report))
    destination = tmp_path / "cow-receipt"
    destination.mkdir(mode=0o700)
    receipt = capacity.prepare_capacity_evidence(
        report_path=changed_root / "report.json",
        fixture_path=changed_root / "fixture.json",
        evidence_root=destination,
        build={k: v for k, v in binding.items() if k != "fixture_manifest_digest"},
        run_id="fixture",
        project="fixture",
        proof_context=context,
    )
    assert any("schema" in error for error in receipt["errors"])
    assert "percentiles_ms" not in receipt


def test_unisolated_capacity_rejects_connection_population_without_receipt(tmp_path, monkeypatch):
    _, _, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=r".+"):
        derive(context, root, artifacts, binding)
    # Strictly typed fixed Summary cannot bypass the unchanged capacity predicate.
    report = dict(
        schema_version=4,
        binding=binding,
        completed_binding=binding,
        started_at="2026-09-21T00:00:00Z",
        finished_at="2026-09-21T00:01:00Z",
        fixture_counts=dict(capacity.FIXTURE_COUNTS),
        attempt_id=context.project().attempt_id,
        protocol_id=context.project().protocol_id,
        protocol_digest=next(row["sha256"] for row in artifacts if row["role"] == "protocol"),
        artifacts=artifacts,
        **summary,
    )
    assert capacity.validate_capacity_report(report, binding, root, proof_context=context)
    path = root / "report.json"
    path.write_text(json.dumps(report))
    destination = tmp_path / "rejected"
    destination.mkdir(mode=0o700)
    receipt = capacity.prepare_capacity_evidence(
        report_path=path,
        fixture_path=root / "fixture.json",
        evidence_root=destination,
        build={k: v for k, v in binding.items() if k != "fixture_manifest_digest"},
        run_id="fixture",
        project="fixture",
        proof_context=context,
    )
    assert receipt["errors"]
    assert "percentiles_ms" not in receipt
    assert not (destination / "capacity-private").exists()


@pytest.mark.parametrize(
    "fault",
    ["family-count", "seal", "cohort", "missing-family", "extra-field", "private-error-key"],
)
def test_changed_whole_safe_projection_never_reaches_capacity(tmp_path, monkeypatch, fault):
    _, _, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    target = "seal" if fault == "seal" else "cleanup" if fault == "cohort" else "c2c"
    path = root / (target + ".json")
    raw = json.loads(path.read_text())
    if fault == "family-count":
        raw["units"][0]["families"]["run-input"]["count"] += 1
    elif fault == "seal":
        raw["metric_version"] = "foreign"
    elif fault == "cohort":
        raw["cohorts"][0]["parity_digest"] = "f" * 64
    elif fault == "missing-family":
        del raw["units"][0]["families"]["run-input"]
    elif fault == "extra-field":
        raw["private_path"] = str(tmp_path)
    else:
        raw["units"][0]["families"][str(tmp_path / "secret-private-original")] = raw["units"][0][
            "families"
        ].pop("run-input")
    data = json.dumps(raw).encode()
    path.write_bytes(data)
    artifact = next(row for row in artifacts if row["role"] == target)
    artifact.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    monkeypatch.setattr(
        capacity,
        "derive_summary",
        lambda *args: pytest.fail("changed originals reached downstream capacity"),
    )
    report = dict(
        schema_version=4,
        binding=binding,
        completed_binding=binding,
        started_at="2026-09-21T00:00:00Z",
        finished_at="2026-09-21T00:01:00Z",
        fixture_counts=dict(capacity.FIXTURE_COUNTS),
        attempt_id=context._bases
        and json.loads((root / "protocol.json").read_text())["attempt_id"],
        protocol_id=json.loads((root / "protocol.json").read_text())["protocol_id"],
        protocol_digest=next(row["sha256"] for row in artifacts if row["role"] == "protocol"),
        artifacts=artifacts,
        **summary,
    )
    errors = capacity.validate_capacity_report(report, binding, root, proof_context=context)
    assert errors
    assert str(tmp_path) not in str(errors)


def test_schema_error_cannot_echo_untrusted_private_dictionary_key(tmp_path, monkeypatch):
    _, _, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    monkeypatch.setattr(capacity, "derive_summary", lambda *args: summary)
    report = derive(context, root, artifacts, binding)
    report["binding"]["images"][str(tmp_path / "secret-private-input")] = None
    errors = capacity.validate_capacity_report(report, binding, root, proof_context=context)
    assert errors
    assert str(tmp_path) not in str(errors)


@pytest.mark.parametrize("supplied", [True, False])
def test_actual_runner_forwards_context_into_real_handoff(tmp_path, monkeypatch, supplied):
    """All external runner effects are existing FakeCommandRunner boundaries."""
    from dataclasses import asdict

    from api.tests.scripts.test_acceptance_runner import FakeCommandRunner, _config
    from scripts.acceptance import runner as module

    _, _, context, root, artifacts, binding, summary = package(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_load_dotenv", lambda path: {})
    monkeypatch.setattr(module.os, "environ", {})
    monkeypatch.setattr(module, "assert_ports_available", lambda ports: None)
    config = _config(tmp_path / "runner")
    commands = FakeCommandRunner(config.evidence_dir)
    repository = tmp_path / "runner-source"
    repository.mkdir()
    fixture_example = repository / "deploy/evaluation/local-fixture.example.json"
    fixture_example.parent.mkdir(parents=True)
    from pathlib import Path

    fixture_example.write_bytes(
        (
            Path(__file__).resolve().parents[2] / "deploy/evaluation/local-fixture.example.json"
        ).read_bytes()
    )
    runner = module.AcceptanceRunner(
        config,
        commands=commands,
        repository_root=repository,
        readiness_probe=lambda url: True,
        capacity_proof_context=context if supplied else None,
    )
    # Existing scoped runner fixtures inject strict-bridge dependencies; they
    # are unrelated to the C2c context/copy work and do not launch resources.
    monkeypatch.setattr(runner, "_prepare_strict_binding", lambda *args: None)
    monkeypatch.setattr(runner, "_validate_strict_receipt", lambda: None)
    binding.update(
        **asdict(runner._capture_git()),
        images=asdict(runner._capture_images()),
        migration=runner._capture_capacity_migration(),
    )
    monkeypatch.setattr(capacity, "derive_summary", lambda roles, actual: summary)
    monkeypatch.setattr(capacity, "budget_errors", lambda derived: [])
    report = derive(context, root, artifacts, binding)
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report))
    runner._environment["ACCEPTANCE_CAPACITY_REPORT"] = str(report_path)
    runner._environment["ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST"] = str(root / "fixture.json")
    calls = []
    original = capacity._proof_context

    def record_context(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(capacity, "_proof_context", record_context)
    status = runner.execute()
    receipt = json.loads((config.evidence_dir / "capacity-validation.json").read_text())
    assert calls[0] is (context if supplied else None)
    if supplied:
        assert status == 0
        assert receipt["errors"] == []
        assert len(calls) == 3
        assert calls[-1] is not context
        assert type(calls[-1]) is type(context)
    else:
        assert status == 1
        assert receipt["errors"] == ["capacity handoff: private C2c original context required"]
        assert "percentiles_ms" not in receipt
