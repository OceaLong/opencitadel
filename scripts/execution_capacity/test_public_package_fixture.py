"""Schema-only public package samples; no original proof or capacity success."""

import hashlib
import json

from scripts.acceptance import capacity
from scripts.acceptance.capacity_c2c_models import (
    HISTORY_FAMILIES,
    OPERAND_FAMILIES,
    PREDICATE_FAMILIES,
    SETTLEMENT_TABLES,
    SOURCE_FAMILIES,
)
from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_models import C2c, Seal

D = "a" * 64


def minimal_public_roles():
    from scripts.execution_capacity.test_seal_finalizer import fixtures

    _, metadata = fixtures("00000000-0000-0000-0000-000000000001")
    seal = Seal(
        **metadata, attempt_id="schema-only", protocol_id="schema-only", images=[], cohorts=[]
    )
    compact = {"count": 0, "sha256": D}
    unit = {
        "schema_version": 2,
        "kind": "base",
        "origin_sha256": D,
        "base_sha256": None,
        "manifest_sha256": D,
        "cleanup_sha256": D,
        "final_sha256": D,
        "originals": {
            "encoding": 2,
            "raw_files": 1,
            "raw_bytes": 1,
            "records": 2,
            "occurrences": 1,
        },
        "families": dict.fromkeys(OPERAND_FAMILIES, compact),
        "source": dict.fromkeys(SOURCE_FAMILIES, compact),
        "tables": dict.fromkeys((*SETTLEMENT_TABLES, "execution_outbox"), compact),
        "history": dict.fromkeys(HISTORY_FAMILIES, compact),
        "predicates": dict.fromkeys(PREDICATE_FAMILIES, compact),
        "objects": compact,
        "sql": compact,
        "transports": compact,
        "writers_sha256": D,
        "storage_sha256": D,
        "broker_sha256": D,
        "physical_sha256": D,
        "physical_observations": compact,
    }
    projection = C2c(
        attempt_id=seal.attempt_id, protocol_id=seal.protocol_id, projection_version=2, units=[unit]
    )
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
    role(
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
    return roles
