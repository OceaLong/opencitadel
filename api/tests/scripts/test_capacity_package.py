"""Small structural package oracles, never full capacity acceptance."""

import hashlib
import itertools
import json
from copy import deepcopy

import pytest
from scripts.acceptance.capacity_c2c_models import (
    HISTORY_FAMILIES,
    OPERAND_FAMILIES,
    PREDICATE_FAMILIES,
    SETTLEMENT_TABLES,
    SOURCE_FAMILIES,
)
from scripts.acceptance.capacity_io import load_artifacts
from scripts.acceptance.capacity_models import Artifact
from scripts.acceptance.capacity_package import PackageSession, PublicConsumerResources
from scripts.execution_capacity.evidence_bounds import EvidenceBudget

D = "a" * 64


def small_roles():
    def role(name, **fields):
        return dict(
            schema_version=3, attempt_id="attempt", protocol_id="protocol", role=name, **fields
        )

    roles = {}
    roles["protocol"] = role(
        "protocol",
        binding_digest=D,
        seal_id="seal",
        registered_ns=1,
        clock_id="clock",
        clock_unit="nanoseconds",
        latency_method="coordinator_predispatch_to_paint_upper_bound",
        rate_rule="each-1s-bin-at-least-2-v1",
        startup_ns=1,
        marker_cadence_ns=1,
        marker_timeout_ns=1,
        marker_count_bound=1,
        marker_max_outstanding=1,
        load_ready_ns=2_000_000_000,
        warm_plan="same-target-explicit-prewarm-before-window",
        diagnostics_timing="after-timed-read-or-separate-clone",
        backend="linux-x86_64-kvm-qemu",
        network_backend="owned-netns-veth-qemu-usernet",
        samples=[],
        windows=[],
    )
    roles["measurements"] = role(
        "measurements",
        **{
            key: []
            for key in (
                "markers",
                "live_paints",
                "samples",
                "sources",
                "browsers",
                "resources",
                "errors",
            )
        },
    )
    roles["workload"] = role(
        "workload", windows=[], progress=[], source_acks=[], errors=["one", "two"]
    )
    roles["resets"] = role("resets", resets=[])
    roles["diagnostics"] = role("diagnostics", queries=[])
    roles["seal"] = role(
        "seal",
        seal_id="seal",
        fixture_id="fixture",
        fixture_manifest_digest=D,
        source_digest=D,
        configuration_digest=D,
        policy_id="policy",
        migration="0022",
        projector_source_version="1",
        read_algorithm_version="1",
        generation="1",
        metric_version="execution-analysis-v2",
        completed_corpus_batch_id="batch",
        images=[],
        cohorts=[],
    )
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
    limits["policy_id"] = "policy"
    roles["environment"] = role(
        "environment",
        server_host_id="server",
        client_host_id="client",
        server_cpu_ids=[0, 1],
        client_cpu_ids=[2],
        host_memory_bytes=10,
        server_memory_bytes=4,
        client_memory_bytes=4,
        overhead_memory_bytes=1,
        postgres_memory_max_bytes=1,
        cgroup_id="group",
        disk_id="disk",
        disk_rotational=False,
        disk_direct_io=True,
        architecture="x86_64",
        os="Linux",
        browser="Chromium",
        postgres="PG",
        python="Python",
        playwright="Playwright",
        qemu_binary_sha256=D,
        viewport=[1440, 900],
        runtime_limits=limits,
    )
    link = {
        "namespace_inode": 1,
        "ifindex": 1,
        "peer_ifindex": 2,
        "address": "192.0.2.1",
        "route_destination": "192.0.2.0/30",
        "route_ifindex": 1,
        "qdisc_kind": "netem",
        "delay_us": 25000,
        "rate_bps": 20000000,
        "default_route": False,
    }
    roles["network"] = role(
        "network",
        backend="owned-netns-veth-qemu-usernet",
        host_link=link,
        client_link=link,
        forward_bound_address="192.0.2.1",
        guest_ports=[8000],
        global_ip_forward_changed=False,
        nat_changed=False,
        cdp_delay_ms=0,
        context_ids=[],
        calibrations=[],
        phase_intervals=[],
        validity={
            "added_rtt_min_ns": 1,
            "added_rtt_max_ns": 2,
            "throughput_min_bps": 1,
            "throughput_max_bps": 2,
        },
    )
    roles["cleanup"] = role(
        "cleanup",
        rounds=[],
        cohorts=[],
        total={"runs": 0, "formal_events": 0, "observations": 0, "visible_steps": 0},
        dispositions=[],
        pending=[],
        quarantined=[],
        status="retained_immutable",
    )
    empty = {"count": 0, "sha256": D}
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
        "objects": empty,
        "sql": empty,
        "transports": empty,
        "physical_observations": empty,
        "writers_sha256": D,
        "storage_sha256": D,
        "broker_sha256": D,
        "physical_sha256": D,
    }
    for name, fields in [
        ("families", OPERAND_FAMILIES),
        ("source", SOURCE_FAMILIES),
        ("tables", (*SETTLEMENT_TABLES, "execution_outbox")),
        ("history", HISTORY_FAMILIES),
        ("predicates", PREDICATE_FAMILIES),
    ]:
        unit[name] = dict.fromkeys(fields, empty)
    roles["c2c"] = role("c2c", projection_version=2, units=[unit])
    roles["fixture"] = {
        "schema_version": 1,
        "fixture_id": "fixture",
        "seed": 1,
        "scope_ids": ["scope"],
        "counts": {},
        "window_start": "start",
        "window_end": "end",
        "status": "planned",
        "target": {
            "environment": "test",
            "runtime_id": "runtime",
            "database_id": "database",
            "scope_id": "scope",
        },
        "ownership_journal": "ownership.jsonl",
    }
    return roles


def resources():
    return PublicConsumerResources(
        EvidenceBudget(bytes_limit=32 * 1024**2, rows_limit=100000, row_limit=1024**2), 1024**2
    )


def write(root, roles):
    artifacts = []
    for name, value in roles.items():
        data = json.dumps(value, sort_keys=True).encode()
        path = name + ".json"
        (root / path).write_bytes(data)
        artifacts.append(
            Artifact(
                path=path,
                role=value["role"] if name != "fixture" else name,
                schema_version=value["schema_version"],
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        )
    return artifacts


def test_original_all_field_oracle_and_closed_lifetime(tmp_path):
    descriptors = write(tmp_path, small_roles())
    expected, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        actual = package.roles
        for name, model in expected.items():
            assert actual[name] == model
            for key in type(model).model_fields:
                assert getattr(actual[name], key) == getattr(model, key)
        rows = actual["workload"].errors
        assert rows[-1] == "two"
        assert rows[::-1] == ["two", "one"]
        assert rows[1:1] == []
        assert actual["environment"].model_dump() == expected["environment"].model_dump()
    with pytest.raises(ValueError, match="closed"):
        len(rows)
    with pytest.raises(ValueError, match="closed"):
        _ = actual["protocol"].attempt_id


def test_multishard_preserves_manifest_order_and_header_oracle(tmp_path):
    roles = small_roles()
    roles["workload-second"] = {**roles["workload"], "errors": ["three"]}
    descriptors = write(tmp_path, roles)
    descriptors = [
        item.model_copy(
            update={"shard_count": 2, "shard_index": 1 if item.path == "workload.json" else 0}
        )
        if item.role == "workload"
        else item
        for item in descriptors
    ]
    expected, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        assert package.roles["workload"] == expected["workload"]
        assert package.roles["workload"].errors == ["one", "two", "three"]
    roles["workload-second"]["attempt_id"] = "other"
    changed = write(tmp_path, roles)
    changed = [
        item.model_copy(
            update={"shard_count": 2, "shard_index": 1 if item.path == "workload.json" else 0}
        )
        if item.role == "workload"
        else item
        for item in changed
    ]
    with pytest.raises(ValueError, match="identity"):
        PackageSession(changed, tmp_path, resources=resources())


def test_cleanup_three_family_shards_match_original_and_refuse_incomplete_headers(tmp_path):
    roles = small_roles()
    cleanup = roles.pop("cleanup")
    origin = {"kind": "base", "seal_id": "seal", "round": None, "boot_id": None, "clone_id": None}
    cohort = {
        "origin": origin,
        "cohort_id": "cohort",
        "kind": "standard",
        "scope_id": "scope",
        "run_ids": [],
        "source": cleanup["total"],
        "view": cleanup["total"],
        "parity_digest": D,
    }
    round_row = {
        "origin": origin,
        "base_inventory_digest": D,
        "cohorts": [cohort],
        "owned_run_count": 0,
        "owned_run_digest": D,
        "errors": [],
    }
    disposition = {
        "resource_id": "resource",
        "kind": "claim",
        "state": "settled",
        "observed_ns": 1,
        "owner_id": "owner",
        "identity_digest": D,
        "physical_slots": 0,
        "proof_kind": "claim-readback",
    }
    for name, field, value in (
        ("cleanup-rounds", "rounds", round_row),
        ("cleanup-cohorts", "cohorts", cohort),
        ("cleanup-dispositions", "dispositions", disposition),
    ):
        roles[name] = {
            **cleanup,
            "total": dict(cleanup["total"]),
            "rounds": [],
            "cohorts": [],
            "dispositions": [],
            field: [value],
        }
    descriptors = write(tmp_path, roles)
    descriptors = [
        row.model_copy(
            update={
                "shard_count": 3,
                "shard_index": ("cleanup-rounds", "cleanup-cohorts", "cleanup-dispositions").index(
                    row.path.removesuffix(".json")
                ),
            }
        )
        if row.role == "cleanup"
        else row
        for row in descriptors
    ]
    old, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        actual = package.roles["cleanup"]
        assert actual == old["cleanup"]
        assert list(actual.rounds) == old["cleanup"].rounds
        assert list(actual.cohorts) == old["cleanup"].cohorts
        assert list(actual.dispositions) == old["cleanup"].dispositions
    missing = [row for row in descriptors if row.path != "cleanup-cohorts.json"]
    with pytest.raises(ValueError, match="missing capacity artifact role/shard"):
        PackageSession(missing, tmp_path, resources=resources())
    duplicate = [
        row.model_copy(update={"shard_index": 0}) if row.path == "cleanup-cohorts.json" else row
        for row in descriptors
    ]
    with pytest.raises(ValueError, match="duplicate/inconsistent artifact shard"):
        PackageSession(duplicate, tmp_path, resources=resources())
    roles["cleanup-cohorts"]["total"]["runs"] = 1
    changed = write(tmp_path, roles)
    changed = [
        row.model_copy(
            update={
                "shard_count": 3,
                "shard_index": ("cleanup-rounds", "cleanup-cohorts", "cleanup-dispositions").index(
                    row.path.removesuffix(".json")
                ),
            }
        )
        if row.role == "cleanup"
        else row
        for row in changed
    ]
    with pytest.raises(ValueError, match="shard role identity mismatch"):
        PackageSession(changed, tmp_path, resources=resources())


def test_network_and_native_binding_shards_preserve_old_order_and_scalar_identity(tmp_path):
    from api.tests.scripts.capacity_population import slots
    from api.tests.scripts.capacity_population_sample import sample_bundle
    from api.tests.scripts.capacity_population_window import network_rows

    roles = small_roles()
    network = roles.pop("network")
    measurement = roles.pop("measurements")
    slot = next(value for value in slots() if value.loaded_window_id is not None)
    rows = list(network_rows(slot))
    families = (
        ("network-contexts", "context_ids", ["context-0", "context-1"]),
        ("network-probes", "calibrations", [row for kind, row in rows if kind == "calibration"]),
        ("network-phases", "phase_intervals", [row for kind, row in rows if kind == "phase"]),
    )
    for name, field, values in families:
        roles[name] = {
            **deepcopy(network),
            "context_ids": [],
            "calibrations": [],
            "phase_intervals": [],
            field: values,
        }
    target = sample_bundle(slot)[0]["target"]
    native = {
        "sample_id": slot.sample_id,
        "action_id": "action",
        "page_id": "page",
        "context_id": "context",
        "clock_id": "host-clock",
        "request_id": "request",
        "response_id": "response",
        "request_ns": "1",
        "received_ns": "2",
        "target": target,
        "session_id": None,
        "batch_id": None,
    }
    roles["measurements-native"] = {**deepcopy(measurement), "native_bindings": [native]}
    roles["measurements-empty"] = {**deepcopy(measurement), "native_bindings": []}
    descriptors = write(tmp_path, roles)
    index = {
        "network-contexts": 0,
        "network-probes": 1,
        "network-phases": 2,
        "measurements-native": 0,
        "measurements-empty": 1,
    }
    descriptors = [
        row.model_copy(
            update={
                "shard_count": 3 if row.role == "network" else 2,
                "shard_index": index[row.path.removesuffix(".json")],
            }
        )
        if row.role in {"network", "measurements"}
        else row
        for row in descriptors
    ]
    old, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        for name, fields in (
            ("network", ("context_ids", "calibrations", "phase_intervals")),
            ("measurements", ("native_bindings",)),
        ):
            assert package.roles[name] == old[name]
            for field in fields:
                assert list(getattr(package.roles[name], field)) == getattr(old[name], field)
    roles["network-phases"]["validity"]["throughput_min_bps"] += 1
    changed = write(tmp_path, roles)
    changed = [
        row.model_copy(
            update={
                "shard_count": 3 if row.role == "network" else 2,
                "shard_index": index[row.path.removesuffix(".json")],
            }
        )
        if row.role in {"network", "measurements"}
        else row
        for row in changed
    ]
    with pytest.raises(ValueError, match="shard role identity mismatch"):
        PackageSession(changed, tmp_path, resources=resources())


def test_late_corrupt_and_original_c2c_validator_refuse(tmp_path):
    roles = small_roles()
    descriptors = write(tmp_path, roles)
    (tmp_path / descriptors[-1].path).write_bytes(b"late-corrupt")
    with pytest.raises(ValueError, match="digest"):
        PackageSession(descriptors, tmp_path, resources=resources())
    roles["c2c"]["units"] *= 2
    descriptors = write(tmp_path, roles)
    with pytest.raises(ValueError, match="duplicated"):
        PackageSession(descriptors, tmp_path, resources=resources())


def context(tmp_path):
    from scripts.execution_capacity.offline_context import BaseLocation, OfflineProofContext

    allowance = resources()
    return OfflineProofContext(
        bases=[BaseLocation(tmp_path / "origin", tmp_path / "retained", tmp_path / "image")],
        rounds=[],
        signing_secret="unit-only",
        cursor_secret=b"unit-only",
        budget=allowance.budget,
        index_bytes=allowance.index_bytes,
    )


def test_trusted_resource_and_default_facade_close_on_predicate_failure(tmp_path, monkeypatch):
    from scripts.acceptance.capacity import derive_capacity_report
    from scripts.execution_capacity.offline_context import OfflineProofContext

    proof = context(tmp_path)
    allowance = proof.public_resources()
    assert proof.public_resources().budget is allowance.budget
    seen = []

    def reject(self, roles, *, package):
        assert self is proof
        assert all(package.owns_role(role, name) for name, role in roles.items())
        seen.append(package)
        raise ValueError("actual predicate boundary failure")

    monkeypatch.setattr(OfflineProofContext, "validate", reject)
    descriptors = write(tmp_path, small_roles())
    binding = {
        "revision": "revision",
        "dirty_tree_digest": D,
        "images": {"api": "image"},
        "migration": "0022",
        "fixture_manifest_digest": D,
    }
    with pytest.raises(ValueError, match="actual predicate boundary failure"):
        derive_capacity_report(
            artifacts=[row.model_dump() for row in descriptors],
            binding=binding,
            completed_binding=binding,
            root=tmp_path,
            started_at="start",
            finished_at="end",
            proof_context=proof,
        )
    assert len(seen) == 1
    assert not seen[0].index.root.exists()
    with pytest.raises(ValueError, match="closed"):
        _ = seen[0].roles


def test_proof_rejects_foreign_role_before_private_replay(tmp_path, monkeypatch):
    from scripts.execution_capacity.offline_context import OfflineProofContext, PrivateProofError

    proof = context(tmp_path)
    descriptors = write(tmp_path, small_roles())

    def unexpected(self):
        pytest.fail("foreign package must refuse before replay")

    monkeypatch.setattr(OfflineProofContext, "_checked", unexpected)
    with (
        PackageSession(descriptors, tmp_path, resources=proof.public_resources()) as first,
        PackageSession(descriptors, tmp_path, resources=proof.public_resources()) as second,
    ):
        roles = first.roles
        roles["c2c"] = second.roles["c2c"]
        with pytest.raises(PrivateProofError, match="actual sealed public package"):
            proof.validate(roles, package=first)


def test_shared_unique_uses_original_rows_same_index_and_duplicate_layer(tmp_path):
    from scripts.acceptance.capacity_physical import unique

    roles = small_roles()

    def ack(identity):
        return {
            "progress_id": identity,
            "clock_id": "clock",
            "received_ns": 3,
            "progress_digest": D,
            "query_before_ns": 1,
            "query_after_ns": 2,
        }

    roles["workload"]["source_acks"] = [ack("second"), ack("first")]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    expected = unique(old["workload"].source_acks, "progress_id")
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        rows = package.roles["workload"].source_acks
        actual = unique(rows, "progress_id")
        assert actual == expected
        assert list(actual) == list(expected)
        assert list(actual.items()) == list(expected.items())
        assert list(actual.values()) == list(expected.values())
        assert actual["first"] == expected["first"]
        assert actual.get("absent") is None
        assert unique(rows, "progress_id") is actual
        assert actual._rows._owner.index is package.index
    with pytest.raises(ValueError, match="closed"):
        actual["first"]
    roles["workload"]["source_acks"].append(ack("second"))
    descriptors = write(tmp_path, roles)
    with (
        PackageSession(descriptors, tmp_path, resources=resources()) as package,
        pytest.raises(ValueError, match="duplicate progress_id"),
    ):
        unique(package.roles["workload"].source_acks, "progress_id")


def test_marker_group_stable_exact_numeric_order_and_next_ownership(tmp_path):
    from scripts.acceptance.capacity_physical import unique
    from scripts.acceptance.capacity_public_groups import NextMarkers, PublicGroups

    roles = small_roles()
    roles["measurements"]["markers"] = [
        {
            "marker_id": "m" + str(i),
            "window_id": window,
            "boot_id": "boot",
            "clock_id": "clock",
            "sequence": sequence,
            "scheduled_ns": 0,
            "sent_ns": 0,
            "completed_ns": 0,
            "installed_ack_ns": 0,
            "guest_installed_ns": i,
            "command_nonce": "nonce",
            "response_nonce": "nonce",
        }
        for i, (window, sequence) in enumerate(
            [("one", 2**60 + 1), ("two", 1), ("one", 2**60), ("one", 2**60)]
        )
    ]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        markers = unique(package.roles["measurements"].markers, "marker_id")
        grouped = PublicGroups(markers, "markers-window")
        expected = sorted(
            [row for row in old["measurements"].markers if row.window_id == "one"],
            key=lambda row: row.sequence,
        )
        rows = grouped[("one",)]
        assert list(rows) == expected
        assert rows[-1] == expected[-1]
        assert list(rows[::-1]) == expected[::-1]
        assert list(grouped[("absent",)]) == []
        assert package.index.query_plans_use_declared_indexes()
        following = NextMarkers(markers)
        for before, after in itertools.pairwise(expected):
            following[before.marker_id] = after.guest_installed_ns
        assert following.get(expected[0].marker_id) == expected[1].guest_installed_ns
        assert following.get(expected[-1].marker_id) is None
    with pytest.raises(ValueError, match="closed"):
        list(rows)


def test_progress_groups_preserve_window_arrival_and_per_run_stable_sort(tmp_path):
    from scripts.acceptance.capacity_physical import unique
    from scripts.acceptance.capacity_public_groups import PublicGroups
    from test_capacity_native import progress

    roles = small_roles()
    roles["workload"]["progress"] = [
        progress(i + 1).model_dump() | {"window_id": window, "run_id": run, "after_ns": at}
        for i, (window, run, at) in enumerate(
            [("w", "r", 4), ("other", "r", 2), ("w", "r", 2), ("w", "s", 1), ("w", "r", 2)]
        )
    ]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        actual = unique(package.roles["workload"].progress, "progress_id")
        by_window = PublicGroups(actual, "progress-window")
        by_run = PublicGroups(actual, "progress-run")
        window = [row for row in old["workload"].progress if row.window_id == "w"]
        assert list(by_window[("w",)]) == window
        assert list(by_run[("w", "r")]) == sorted(
            [row for row in window if row.run_id == "r"], key=lambda row: row.after_ns
        )
        assert list(by_run[("other", "r")]) == [old["workload"].progress[1]]
        assert list(by_run[("missing", "r")]) == []


def test_shared_window_predicate_keeps_orphan_error_layer(tmp_path):
    from scripts.acceptance.capacity_derive import validate_windows
    from test_capacity_native import progress

    roles = small_roles()
    roles["workload"]["errors"] = []
    roles["workload"]["progress"] = [progress(1).model_dump()]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    with pytest.raises(ValueError, match=r"^orphan progress window$"):
        validate_windows(old, {})
    with (
        PackageSession(descriptors, tmp_path, resources=resources()) as package,
        pytest.raises(ValueError, match=r"^orphan progress window$"),
    ):
        validate_windows(package.roles, {})


def test_public_progress_sets_match_original_and_foreign_empty_refuses(tmp_path):
    from scripts.acceptance.capacity_public_ids import PublicIDs

    descriptors = write(tmp_path, small_roles())
    with (
        PackageSession(descriptors, tmp_path, resources=resources()) as package,
        PackageSession(descriptors, tmp_path, resources=resources()) as other,
    ):
        left = PublicIDs(package, "eligible-progress", ["b", "a", "b", "\x00", "😀"])
        right = PublicIDs(package, "paint-progress", ["a", "b", "c", "\x00", "😀"])
        assert left == {"a", "b", "\x00", "😀"}
        assert left <= right
        assert not right <= left
        assert left | right == {"a", "b", "c", "\x00", "😀"}
        with pytest.raises(ValueError, match="foreign"):
            _ = PublicIDs(package, "ack-progress") == PublicIDs(other, "ack-progress")


def test_shared_progress_receipt_preserves_missing_paint_layer(tmp_path):
    from scripts.acceptance.capacity_physical import unique
    from scripts.acceptance.capacity_timing import validate_progress_receipts
    from test_capacity_native import progress

    roles = small_roles()
    roles["workload"]["progress"] = [progress(1).model_dump()]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)

    def check(actual):
        with pytest.raises(
            ValueError, match="every effective measured progress requires native paint"
        ):
            validate_progress_receipts(
                unique(actual["workload"].progress, "progress_id"),
                unique(actual["measurements"].live_paints, "progress_id"),
                actual["workload"].source_acks,
                {},
                "clock",
            )

    check(old)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        check(package.roles)


def test_public_sorted_union_digest_exact_old_json_order(tmp_path):
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.acceptance.capacity_public_ids import PublicIDs

    descriptors = write(tmp_path, small_roles())
    values = ["\x00", "a", "aa", "a\x00", "😀", "\ud800", "\uffff", '"', "\\"]
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        left = PublicIDs(package, "cohort-runs", values[::-1])
        right = PublicIDs(package, "cohort-runs", ["other", "a"])
        assert (left | right).sorted_digest() == canonical_digest(
            sorted(set(values) | {"other", "a"})
        )
        assert left.intersects(right)


@pytest.mark.parametrize("round_count", [0, 2])
def test_actual_base_cohort_join_matches_original_and_rejects_hidden_member(tmp_path, round_count):
    from scripts.acceptance.capacity_physical import join_inventories

    roles = small_roles()
    origin = {"kind": "base", "seal_id": "seal", "round": None, "boot_id": None, "clone_id": None}
    counts = {"runs": 1, "formal_events": 1, "observations": 1, "visible_steps": 0}
    cohorts = [
        {
            "cohort_id": kind,
            "kind": kind,
            "scope_id": kind,
            "run_ids": [kind],
            "origin": origin,
            "source": counts,
            "view": counts,
            "parity_digest": D,
        }
        for kind in ("standard", "step_capacity", "evaluation_subject", "evaluation_judge")
    ]
    roles["seal"]["cohorts"] = cohorts
    roles["cleanup"]["cohorts"] = list(cohorts)
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.acceptance.capacity_models import Cohort

    for i in range(round_count):
        binding = {
            "schema_version": 1,
            "parent_attempt_id": "attempt",
            "round_id": f"round{i}",
            "sample_id": f"sample{i}",
            "window_id": f"window{i}",
            "parent_plan_digest": D,
            "child_plan_digest": D,
            "reservation_digest": D,
            "child_origin_sha256": D,
        }
        round_origin = {
            "kind": "round",
            "seal_id": "seal",
            "round": binding,
            "boot_id": f"boot{i}",
            "clone_id": f"round{i}",
        }
        cohort = {
            "cohort_id": f"live{i}",
            "kind": "live",
            "scope_id": f"scope{i}",
            "run_ids": [f"run{i}"],
            "origin": round_origin,
            "source": counts,
            "view": counts,
            "parity_digest": D,
        }
        roles["cleanup"]["rounds"].append(
            {
                "origin": round_origin,
                "base_inventory_digest": canonical_digest(
                    [Cohort.model_validate(c).model_dump() for c in cohorts]
                ),
                "cohorts": [cohort],
                "owned_run_count": 5,
                "owned_run_digest": canonical_digest(
                    sorted([c["run_ids"][0] for c in cohorts] + [f"run{i}"])
                ),
                "errors": [],
            }
        )
        roles["cleanup"]["cohorts"].append(cohort)
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    expected = join_inventories(
        "seal", old["seal"].cohorts, old["cleanup"].rounds, old["cleanup"].cohorts, "attempt"
    )
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        actual = package.roles
        assert (
            join_inventories(
                "seal",
                actual["seal"].cohorts,
                actual["cleanup"].rounds,
                actual["cleanup"].cohorts,
                "attempt",
            )
            == expected
        )
    roles["cleanup"]["cohorts"] = cohorts[:-1]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    with pytest.raises(ValueError, match="cleanup hidden/missing"):
        join_inventories(
            "seal", old["seal"].cohorts, old["cleanup"].rounds, old["cleanup"].cohorts, "attempt"
        )
    with (
        PackageSession(descriptors, tmp_path, resources=resources()) as package,
        pytest.raises(ValueError, match="cleanup hidden/missing"),
    ):
        join_inventories(
            "seal",
            package.roles["seal"].cohorts,
            package.roles["cleanup"].rounds,
            package.roles["cleanup"].cohorts,
            "attempt",
        )


@pytest.mark.parametrize(
    "values", [[], [1, 2, 2, 3], [2**100, 2**100 + 1, 2**100, 1], [0.0, 201.0, 200, 201.0]]
)
def test_compact_numeric_exact_old_arrays_oracle(tmp_path, values):
    from scripts.acceptance.capacity_compact import Numbers
    from scripts.acceptance.capacity_derive import percentile
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.acceptance.capacity_models import FrameSummary, LongTaskSummary

    descriptors = write(tmp_path, small_roles())
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        numbers = Numbers(package)
        for value in values:
            numbers.add(value)
        result = numbers.result()
        assert result == {
            "count": len(values),
            "p50_ms": percentile(values, 0.5) if values else None,
            "p95_ms": percentile(values, 0.95) if values else None,
            "max_ms": max(values) if values else None,
            "ordered_sha256": canonical_digest(values),
        }
        FrameSummary.model_validate(result)
        tasks = numbers.result(long_tasks=True)
        assert tasks["over_200ms_count"] == sum(value > 200 for value in values)
        LongTaskSummary.model_validate(tasks)


def test_compact_schema_rejects_missing_extra_mixed_and_stale_version():
    from pydantic import ValidationError
    from scripts.acceptance.capacity_models import FrameSummary, LongTaskSummary, Report

    row = {"count": 1, "p50_ms": 1, "p95_ms": 1, "max_ms": 1, "ordered_sha256": D}
    for changed in [
        row | {"samples_ms": [1]},
        row | {"count": 0},
        row | {"p50_ms": None},
        {key: value for key, value in row.items() if key != "ordered_sha256"},
    ]:
        with pytest.raises(ValidationError):
            FrameSummary.model_validate(changed)
    with pytest.raises(ValidationError):
        LongTaskSummary.model_validate(
            {"count": 1, "over_200ms_count": 0, "max_ms": 201, "ordered_sha256": D}
        )
    with pytest.raises(ValidationError) as caught:
        Report.model_validate({"schema_version": 3})
    assert any(
        error["loc"] == ("schema_version",) and error["type"] == "literal_error"
        for error in caught.value.errors()
    )


def test_real_budget_rejects_empty_frames_but_accepts_empty_long_tasks():
    from scripts.acceptance.capacity_derive import budget_errors
    from scripts.acceptance.capacity_io import canonical_digest

    # Isolated arithmetic gate: no fake budget_errors and no acceptance claim.
    summary = {
        "warm": {},
        "cold": {},
        "step_capacity": {"warm": {}, "cold": {}},
        "latency": {"admission_baseline_ms": [1], "admission_loaded_ms": [1]},
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
    }
    assert budget_errors(summary) == []
    summary["frames"].update(
        count=0, p50_ms=None, p95_ms=None, max_ms=None, ordered_sha256=canonical_digest([])
    )
    assert budget_errors(summary) == ["frame p95 exceeds 33ms"]


@pytest.mark.parametrize("mode", ["round-window", "round-sample", "provider-calls"])
def test_fixed_public_lookup_preserves_first_key_last_row_and_closed(tmp_path, mode):
    from scripts.acceptance.capacity_public_lookup import PublicLookup

    roles = small_roles()
    if mode == "provider-calls":
        values = [
            {
                "resource_id": key,
                "kind": "provider_call",
                "state": "settled",
                "observed_ns": i,
                "owner_id": "owner",
                "identity_digest": D,
                "physical_slots": 0,
                "proof_kind": "settlement-readback",
            }
            for i, key in enumerate(["b", "a", "b"])
        ]
        roles["cleanup"]["dispositions"] = values
        field = "dispositions"

        def key(row):
            return row.resource_id
    else:
        values = []
        for i, identity in enumerate(["b", "a", "b"]):
            binding = {
                "schema_version": 1,
                "parent_attempt_id": "attempt",
                "round_id": "round" + str(i),
                "sample_id": identity,
                "window_id": identity,
                "parent_plan_digest": D,
                "child_plan_digest": D,
                "reservation_digest": D,
                "child_origin_sha256": D,
            }
            values.append(
                {
                    "origin": {
                        "kind": "round",
                        "seal_id": "seal",
                        "round": binding,
                        "boot_id": "boot" + str(i),
                        "clone_id": "round" + str(i),
                    },
                    "base_inventory_digest": D,
                    "cohorts": [],
                    "owned_run_count": 0,
                    "owned_run_digest": D,
                    "errors": ["value" + str(i)],
                }
            )
        roles["cleanup"]["rounds"] = values
        field = "rounds"

        def key(row):
            return (
                row.origin.round.window_id if mode == "round-window" else row.origin.round.sample_id
            )

    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    expected = {key(row): row for row in getattr(old["cleanup"], field)}
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        lookup = PublicLookup(getattr(package.roles["cleanup"], field), mode)
        assert list(lookup) == list(expected)
        assert len(lookup) == 2
        assert list(lookup.items()) == list(expected.items())
        assert list(lookup.values()) == list(expected.values())
        with pytest.raises(KeyError):
            lookup["missing"]
    with pytest.raises(ValueError, match="closed"):
        lookup["a"]


@pytest.mark.parametrize("physical", ["baseline-physical", "wrong"])
def test_actual_baseline_round_lookup_keeps_old_binding_gate(tmp_path, physical):
    from scripts.acceptance.capacity_models import Plan
    from scripts.acceptance.capacity_physical import join_round_windows
    from scripts.execution_capacity.test_inventory_contract import fixture, parse, sample_plan

    roles = small_roles()
    _, _, row = fixture()
    row["origin"]["round"]["window_id"] = physical
    row["cohorts"][0]["kind"] = "admission"
    _, _, typed = parse(*fixture()[:2], row)
    roles["cleanup"]["rounds"] = [typed.model_dump()]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    plans = {"sample": Plan.model_validate(sample_plan())}

    def check(rows):
        if physical == "wrong":
            with pytest.raises(
                ValueError, match="baseline/loaded physical round inventory incomplete"
            ):
                join_round_windows(plans, rows, {}, "attempt")
        else:
            join_round_windows(plans, rows, {}, "attempt")

    check(old["cleanup"].rounds)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        check(package.roles["cleanup"].rounds)


@pytest.mark.parametrize("kind", ["live", "admission"])
def test_retained_run_id_sets_preserve_window_error_layer(tmp_path, kind):
    from scripts.acceptance.capacity_derive import validate_windows
    from scripts.execution_capacity.test_inventory_contract import fixture, parse

    roles = small_roles()
    roles["workload"]["errors"] = []
    base, retained, raw = fixture()
    raw["cohorts"][0]["kind"] = kind
    _, _, row = parse(base, retained, raw)
    roles["cleanup"]["rounds"] = [row.model_dump()]
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    message = (
        "live source inventory/window mismatch"
        if kind == "live"
        else "admission source inventory/sample mismatch"
    )
    with pytest.raises(ValueError, match=message):
        validate_windows(old, {})
    with (
        PackageSession(descriptors, tmp_path, resources=resources()) as package,
        pytest.raises(ValueError, match=message),
    ):
        validate_windows(package.roles, {})


@pytest.mark.parametrize(
    "fault", [None, "missing-phase", "duplicate-interval", "duplicate-ordinal"]
)
def test_actual_calibration_group_old_oracle_and_error_layers(tmp_path, fault):
    from types import SimpleNamespace

    from scripts.acceptance.capacity_network import validate_calibrations

    roles = small_roles()
    rows, intervals = [], []
    for phase_index, phase in enumerate(["baseline", "pre", "window", "post"]):
        start = phase_index * 100_000_000_000
        intervals.append(
            {
                "window_id": "w",
                "clock_id": "clock",
                "phase": phase,
                "scheduled_ns": start,
                "before_ns": start,
                "after_ns": start + 18_000_000_000,
                "deadline_ns": start + 30_000_000_000,
            }
        )
        for ordinal in range(18):
            action = "echo" if ordinal < 16 else "upload" if ordinal == 16 else "download"
            size = 32 if action == "echo" else 8 * 1024**2
            rows.append(
                {
                    "window_id": "w",
                    "clock_id": "clock",
                    "phase": phase,
                    "ordinal": ordinal,
                    "action": action,
                    "start_ns": start + ordinal * 1_000_000_000,
                    "end_ns": start + (ordinal + 1) * 1_000_000_000,
                    "transport": "tcp",
                    "bytes": size,
                    "elapsed_ns": 1_000_000_000,
                    "echo_rtt_ns": (1 if phase == "baseline" else 2) if action == "echo" else None,
                    "bits_per_second": size * 8,
                    "coverage": "discrete-probe",
                }
            )
    if fault == "missing-phase":
        rows = [row for row in rows if row["phase"] != "post"]
    if fault == "duplicate-interval":
        intervals.append(intervals[0])
    if fault == "duplicate-ordinal":
        rows[1]["ordinal"] = 0
    roles["network"]["calibrations"] = rows[::-1]
    roles["network"]["phase_intervals"] = intervals
    roles["network"]["validity"].update(
        throughput_min_bps=8 * 8 * 1024**2, throughput_max_bps=8 * 8 * 1024**2
    )
    descriptors = write(tmp_path, roles)
    old, _ = load_artifacts(descriptors, tmp_path)
    windows = {
        "w": SimpleNamespace(
            coordinator_start_ns=200_000_000_000, coordinator_end_ns=250_000_000_000
        )
    }
    plans = {
        "w": SimpleNamespace(
            calibration_window=SimpleNamespace(offset_ns=0, budget_ns=30_000_000_000)
        )
    }
    errors = {
        "missing-phase": "missing discrete calibration phase",
        "duplicate-interval": "missing/duplicate actual calibration phase intervals",
        "duplicate-ordinal": "incomplete calibration probes",
    }

    def check(network):
        if fault is None:
            validate_calibrations(network, windows, "clock", plans)
        else:
            with pytest.raises(ValueError, match=errors[fault]):
                validate_calibrations(network, windows, "clock", plans)

    check(old["network"])
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        check(package.roles["network"])


@pytest.mark.parametrize(
    "fault", [None, "changed-unit", "extra-unit", "missing-original", "stale-comparator"]
)
def test_private_replay_comparator_complete_fields_eof_and_owner(tmp_path, fault):
    from scripts.execution_capacity.offline_context import PrivateProofError

    roles = small_roles()
    descriptors = write(tmp_path, roles)
    original, _ = load_artifacts(descriptors, tmp_path)
    if fault == "extra-unit":
        roles["c2c"]["units"].append({**roles["c2c"]["units"][0], "origin_sha256": "b" * 64})
        descriptors = write(tmp_path, roles)
    with PackageSession(descriptors, tmp_path, resources=resources()) as package:
        comparator = package.start_private_comparison()
        unit = original["c2c"].units[0]
        if fault == "changed-unit":
            unit = unit.model_copy(update={"cleanup_sha256": "f" * 64})
        if fault == "stale-comparator":
            package.start_private_comparison()
            with pytest.raises(ValueError, match="inactive"):
                comparator.base(original["seal"], unit)
            return

        def compare():
            if fault != "missing-original":
                comparator.base(original["seal"], unit)
            comparator.finish([])

        if fault is None:
            compare()
        else:
            with pytest.raises(PrivateProofError, match="complete safe projection"):
                compare()
