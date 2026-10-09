"""Actual child ledger and retained nonempty diagnostic originals, no PG service."""

import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from scripts.execution_capacity.test_pg_diagnostics import original_timed_inputs


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "statement",
        "errors",
        "capture",
        "current",
        "trace",
        "missing-dispatch",
        "parent-dispatch",
        "request",
        "native",
    ],
)
def test_actual_child_diagnostic_replay_and_complete_query_binding(tmp_path, monkeypatch, fault):
    from scripts.acceptance.capacity_models import Measurements, Plan, Sample, Source, SourceOrigin
    from scripts.execution_capacity.attempt import (
        AttemptLedger,
        ReadOnlyAttemptLedger,
        digest,
        encode,
    )
    from scripts.execution_capacity.evidence_owner import copy_original
    from scripts.execution_capacity.pg_diagnostics import authorize_request
    from scripts.execution_capacity.pg_diagnostics_timed import collect_original
    from scripts.execution_capacity.reference_round import reserve_round, verify_round_records
    from scripts.execution_capacity.retained_diagnostics import round_diagnostics

    capture, metadata, observer, _, inventory = original_timed_inputs(tmp_path, monkeypatch)
    budget = metadata.budget
    clock = {
        "boot_id": "fixture-host",
        "clock": "CLOCK_MONOTONIC",
        "namespace_device": 1,
        "namespace_inode": 2,
    }
    monkeypatch.setattr("scripts.execution_capacity.attempt.host_clock", lambda: clock)
    target = {
        "scope_id": "scope",
        "run_id": "run",
        "public_id": "public",
        "revision": "1",
        "step_id": None,
    }
    plan = Plan(
        sample_id="sample",
        dimension="standard",
        mode="warm",
        operation="history",
        ordinal=0,
        target=target,
        window_id="window",
        physical_window_id="window",
        reset_id=None,
        prewarm_completed_ns=None,
        action_id="action",
        page_id="page",
        context_id="context",
    )
    sample = Sample(
        sample_id="sample",
        clock_id="host",
        start_ns=10,
        end_ns=100,
        status="ok",
        error=None,
        action_id="action",
        trigger_ns=10,
        source_id="source",
        browser_id="browser",
    )
    rid = str(uuid4())
    parent_plan = {
        "attempt_id": "parent",
        "protocol_id": "protocol",
        "samples": [plan.model_dump()],
    }
    with AttemptLedger.create(tmp_path / "parent", parent_plan) as parent:
        (parent.root / "rounds").mkdir(mode=0o700)
        child_root = parent.root / "rounds" / rid
        child_plan = {
            "attempt_id": rid,
            "protocol_id": "protocol",
            "samples": [plan.model_dump()],
            "round": {
                "parent_attempt_id": "parent",
                "round_id": rid,
                "sample_id": "sample",
                "window_id": "window",
            },
        }
        binding = reserve_round(parent, child_plan, child_root, seal_digest="a" * 64)
        with AttemptLedger.create(child_root, child_plan) as child:
            child.bind_clock()
            origin = SourceOrigin(
                kind="round", seal_id="seal", round=binding.safe(), boot_id="boot", clone_id="clone"
            )
            dispatch = {
                "command_id": "command",
                "identity": {
                    "sample_id": "sample",
                    "action_id": "action",
                    "window_id": "window",
                    "round_id": rid,
                    "boot_id": "boot",
                    "clone_id": "clone",
                    "observer_clock_id": "guest",
                },
                "host_ns": 120,
                "clock_id": "host",
                "sample_end_ns": 100,
                "operation": "history",
            }
            child.append("pg-diagnostics-dispatch", dispatch)
            request = authorize_request(child, plan, sample, origin, command_id="command")
            result = asyncio.run(collect_original(capture, metadata, observer, request))
            assert not result.errors
            assert not result.statements[0].errors
            retained = copy_original(result, budget=budget)
            if fault == "statement":
                retained["statements"][0]["sql_digest"] = "f" * 64
            elif fault == "errors":
                retained["errors"] = ["failure"]
            elif fault == "capture":
                retained["private"]["capture"]["statements"][0]["parameters"] = ["changed"]
            elif fault == "current":
                retained["private"]["current"]["database_system_identifier"] = "foreign"
            elif fault == "trace":
                retained["private"]["traces"][0] = retained["private"]["traces"][0].replace(
                    "SELECT target", "SELECT foreign"
                )
            elif fault == "request":
                retained["request"]["clock_id"] = "foreign"
            child.append(
                "pg-diagnostics-result",
                {
                    "command_id": "command",
                    "identity": request.identity,
                    "clock_id": "host",
                    "host_ns": 140,
                    "request_digest": request.request_digest,
                    "payload_digest": digest(result.payload(budget=metadata.budget)),
                },
            )
            source = Source(
                source_id="source",
                sample_id="sample",
                target=target,
                clock_id="host",
                submitted_ns=10,
                acknowledged_ns=20,
                readback_ns=90,
                kind="history_revision",
                public_event_id=None,
                sequence=None,
                progress_id=None,
                marker_id=None,
                captured_runs=1,
                matrix_results=0,
                admission_session_id=None,
                admission_run_id=None,
                profile="acceptance-capacity",
                policy_id="policy",
                repository_capture_digest=result.capture_digest,
                repository_clock_id="guest",
                database_identity_digest=result.database_digest,
                build_inventory_digest=result.build_digest,
            )
            native = Measurements(
                attempt_id="parent",
                protocol_id="protocol",
                markers=[],
                live_paints=[],
                samples=[sample],
                sources=[source],
                browsers=[],
                resources=[],
                errors=[],
            ).model_dump(mode="json")
            raw = encode(native)
            path = child.root / "native-window.json"
            path.write_bytes(raw)
            path.chmod(0o600)
            child.append(
                "native-complete-observed",
                {
                    "identity": {"attempt_id": rid, "sample_id": "sample", "window_id": "window"},
                    "host_ns": 110,
                    "artifact": path.name,
                    "digest": digest(native),
                    "round_binding": binding.model_dump(),
                },
            )
            if fault == "native":
                path.write_bytes(raw.replace(b"protocol", b"foreign"))
        if fault == "parent-dispatch":
            parent.append("pg-diagnostics-dispatch", dispatch)
    opened_parent = ReadOnlyAttemptLedger.open(parent.root, origin=parent.root, budget=budget)
    opened_child = ReadOnlyAttemptLedger.open(child.root, origin=child.root, budget=budget)
    verify_round_records(opened_parent, opened_child)
    if fault == "missing-dispatch":
        # No dispatch for this claimed command exists in the independently opened ledger.
        retained["request"]["command_id"] = "absent"
    if fault:
        with pytest.raises((ValueError, KeyError, TypeError), match=r".+"):
            round_diagnostics(
                opened_parent, opened_child, binding, origin, inventory, [retained], budget=budget
            )
    else:
        queries, files = round_diagnostics(
            opened_parent, opened_child, binding, origin, inventory, [retained], budget=budget
        )
        assert queries == [result.export(opened_child, budget=budget)]
        assert set(files) == {"native-window.json"}
        assert "private-signed-body" not in str(queries)
        assert str(tmp_path) not in str(queries)


def diagnostic_plan():
    from scripts.acceptance.capacity_models import Plan

    return Plan(
        sample_id="fixture-sample",
        dimension="standard",
        mode="warm",
        operation="history",
        ordinal=0,
        target={
            "scope_id": "scope",
            "run_id": "run",
            "public_id": "public",
            "revision": "1",
            "step_id": None,
        },
        window_id="fixture-window",
        physical_window_id="fixture-window",
        reset_id=None,
        prewarm_completed_ns=None,
        action_id="action",
        page_id="page",
        context_id="context",
    )


def diagnostic_job(tmp_path, monkeypatch, parent, child, binding, origin, base_value):
    """Actual OriginalDiagnostics job, fixture driver only; real child records."""
    # This helper is entered by acquire_round in its event loop; only the fixture's
    # metadata setup is synchronous and uses its own thread-local asyncio runner.
    import concurrent.futures

    from scripts.acceptance.capacity_models import Measurements, Sample, Source
    from scripts.execution_capacity.attempt import digest, encode
    from scripts.execution_capacity.cumulative_cleanup import OriginalDiagnostics
    from scripts.execution_capacity.pg_diagnostics import authorize_request
    from scripts.execution_capacity.pg_diagnostics_timed import capture_digest

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        capture, metadata, observer, _, inventory = pool.submit(
            original_timed_inputs,
            tmp_path,
            monkeypatch,
            inventory_override=base_value.final["source"],
            budget=base_value.budget,
            parameters=(UUID(int=99),),
        ).result()
    capture.sample_id = binding.sample_id
    capture.clone_id = origin.clone_id
    plan = diagnostic_plan()
    sample = Sample(
        sample_id=binding.sample_id,
        clock_id="host",
        start_ns=10,
        end_ns=100,
        status="ok",
        error=None,
        action_id="action",
        trigger_ns=10,
        source_id="source",
        browser_id="browser",
    )
    source = Source(
        source_id="source",
        sample_id=binding.sample_id,
        target=plan.target,
        clock_id="host",
        submitted_ns=10,
        acknowledged_ns=20,
        readback_ns=90,
        kind="history_revision",
        public_event_id=None,
        sequence=None,
        progress_id=None,
        marker_id=None,
        captured_runs=1,
        matrix_results=0,
        admission_session_id=None,
        admission_run_id=None,
        profile="acceptance-capacity",
        policy_id="policy",
        repository_capture_digest=capture_digest(capture),
        repository_clock_id="guest",
        database_identity_digest=digest(inventory.database),
        build_inventory_digest=inventory.build["digest"],
    )
    native = Measurements(
        attempt_id=binding.parent_attempt_id,
        protocol_id=child.plan["protocol_id"],
        markers=[],
        live_paints=[],
        samples=[sample],
        sources=[source],
        browsers=[],
        resources=[],
        errors=[],
    ).model_dump(mode="json")
    path = child.root / ("native-" + binding.window_id + ".json")
    path.write_bytes(encode(native))
    path.chmod(0o600)
    child.append(
        "native-complete-observed",
        {
            "identity": {
                "attempt_id": binding.round_id,
                "sample_id": binding.sample_id,
                "window_id": binding.window_id,
            },
            "host_ns": 110,
            "artifact": path.name,
            "digest": digest(native),
            "round_binding": binding.model_dump(),
        },
    )
    dispatch = {
        "command_id": "fixture-diagnostic",
        "identity": {
            "sample_id": binding.sample_id,
            "action_id": "action",
            "window_id": binding.window_id,
            "round_id": binding.round_id,
            "boot_id": origin.boot_id,
            "clone_id": origin.clone_id,
            "observer_clock_id": "guest",
        },
        "host_ns": 120,
        "clock_id": "host",
        "sample_end_ns": 100,
        "operation": "history",
    }
    child.append("pg-diagnostics-dispatch", dispatch)
    request = authorize_request(child, plan, sample, origin, command_id="fixture-diagnostic")
    original = OriginalDiagnostics(capture, metadata, observer, request)

    class Job:
        async def collect(self):
            result = await original.collect()
            child.append(
                "pg-diagnostics-result",
                {
                    "command_id": request.command_id,
                    "identity": request.identity,
                    "clock_id": request.clock_id,
                    "host_ns": 140,
                    "request_digest": request.request_digest,
                    "payload_digest": digest(result.payload(budget=metadata.budget)),
                },
            )
            return result

    return Job()


def diagnostic_context_roles(projection, seal, rounds, origins, queries):
    from scripts.acceptance.capacity_models import Cleanup, Diagnostics, Protocol, Window, Workload

    identity = {"attempt_id": seal.attempt_id, "protocol_id": seal.protocol_id}
    cohorts = [*seal.cohorts, *(cohort for row in rounds for cohort in row.cohorts)]
    counts = {
        name: sum(getattr(row.source, name) for row in cohorts)
        for name in ("runs", "formal_events", "observations", "visible_steps")
    }
    windows = []
    for origin in origins:
        times = dict.fromkeys(
            (
                "guest_minimal_ready_ns",
                "guest_start_ns",
                "guest_end_ns",
                "guest_cohort_ns",
                "guest_ready_ns",
                "guest_done_ns",
                "guest_measurement_closed_ns",
                "host_measurement_closed_received_ns",
                "host_metadata_received_ns",
                "host_cohort_received_ns",
                "host_ready_sent_ns",
                "host_done_sent_ns",
                "host_done_received_ns",
                "coordinator_start_ns",
                "coordinator_end_ns",
            ),
            0,
        )
        windows.append(
            Window(
                round_origin=origin,
                window_id=origin.window_id,
                boot_id="fixture",
                **times,
                native_ready_digest="a" * 64,
                native_done_digest="a" * 64,
                snapshots=[],
                clock_id="host",
                context_ids=[],
                session_ids=[],
                claims=[],
                batch_id="fixture",
                suite_version="fixture",
                batch_results=5000,
                ticks=[],
            )
        )
    protocol = Protocol(
        **identity,
        binding_digest="a" * 64,
        seal_id=seal.seal_id,
        registered_ns=1,
        clock_id="host",
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
        samples=[diagnostic_plan()],
        windows=[],
    )
    return {
        "c2c": projection,
        "seal": seal,
        "cleanup": Cleanup(
            **identity,
            rounds=rounds,
            cohorts=cohorts,
            total=counts,
            dispositions=[],
            pending=[],
            quarantined=[],
            status="retained_immutable",
        ),
        "workload": Workload(**identity, windows=windows, progress=[], source_acks=[], errors=[]),
        "protocol": protocol,
        "diagnostics": Diagnostics(**identity, queries=queries),
    }


def test_nonempty_diagnostic_through_actual_context_copy_and_query_projection(
    tmp_path, monkeypatch
):
    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        acquire_round,
        seal_acquired,
    )

    with monkeypatch.context() as first:
        value = asyncio.run(acquire_final(tmp_path, first))
    base = seal_acquired(tmp_path, monkeypatch, value)
    with monkeypatch.context() as second:
        _round_value, location = asyncio.run(
            acquire_round(tmp_path, second, base, value, diagnostic=True)
        )
        context = OfflineProofContext(
            bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
            rounds=[location],
            signing_secret=value.secret,
            cursor_secret=value.cursor,
            budget=value.budget,
        )
        projection, seal, rounds, origins, queries = context._checked()
        assert len(queries) == 1
        roles = diagnostic_context_roles(projection, seal, rounds, origins, queries)
        context.validate(roles)
        copied = context.copy(tmp_path / "diagnostic-copy")
        copied.validate(roles)
        native = next((tmp_path / "diagnostic-copy").rglob("native-fixture-window.json"))
        assert native.is_file()
        changed = deepcopy(roles)
        changed["diagnostics"].queries[0].statements[0].sql_digest = "f" * 64
        with pytest.raises(PrivateProofError, match="safe projection"):
            copied.validate(changed)
        # Recommit the copied original UUID as same-text string. JSON/capture/Query
        # compatibility commitments remain identical, while the complete C2c
        # typed cleanup commitment must detect the changed original type.
        rewrite_typed_diagnostic_parameter(copied._rounds[0].child_retained)
        changed_projection, _, _, _, changed_queries = copied._checked()
        assert changed_queries == queries
        assert changed_projection.units[1].cleanup_sha256 != projection.units[1].cleanup_sha256
        with pytest.raises(PrivateProofError, match="safe projection"):
            copied.validate(roles)
        native.write_bytes(b"{}")
        with pytest.raises(PrivateProofError, match="original replay"):
            copied.project()


@pytest.mark.parametrize("choice", ["current", "base", "foreign", "ambiguous", "changed"])
def test_diagnostic_inventory_requires_unique_complete_verified_snapshot(choice):
    from types import SimpleNamespace

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.pg_diagnostics_timed import diagnostic_inventory

    current = SimpleNamespace(
        database={"database_name": "fixture", "reads": [{"ordinal": 1}]}, build={"digest": "a" * 64}
    )
    base = SimpleNamespace(
        database={"database_name": "fixture", "reads": [{"ordinal": 0}]}, build={"digest": "a" * 64}
    )
    wanted = base if choice == "base" else current
    result = {
        "database_digest": canonical_digest(wanted.database),
        "build_digest": wanted.build["digest"],
    }
    if choice == "foreign":
        result["database_digest"] = "f" * 64
    elif choice == "changed":
        result["build_digest"] = "f" * 64
    elif choice == "ambiguous":
        base = deepcopy(current)
    if choice in {"foreign", "ambiguous", "changed"}:
        with pytest.raises(ValueError, match="unique verified"):
            diagnostic_inventory(result, current, base, budget=EvidenceBudget())
    else:
        assert diagnostic_inventory(result, current, base, budget=EvidenceBudget()) is wanted


def test_typed_diagnostic_parameters_remain_original_and_public_payload_is_safe(
    tmp_path, monkeypatch
):
    from uuid import UUID

    from scripts.execution_capacity.evidence_owner import copy_original
    from scripts.execution_capacity.pg_diagnostics_timed import collect_original, replay_original

    parameters = (UUID(int=99),)
    capture, metadata, observer, request, inventory = original_timed_inputs(
        tmp_path, monkeypatch, parameters=parameters
    )
    result = asyncio.run(collect_original(capture, metadata, observer, request))
    assert result.private["capture"]["statements"][0]["parameters"] == list(parameters)
    public = result.payload(budget=metadata.budget)
    assert str(parameters[0]) not in str(public)
    retained = copy_original(result, budget=metadata.budget)
    assert (
        replay_original(retained, inventory, budget=metadata.budget).payload(budget=metadata.budget)
        == public
    )

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.evidence_json import json_digest

    assert result.payload(budget=metadata.budget)["private_digest"] == json_digest(
        result.private, budget=metadata.budget
    )
    with pytest.raises(ValueError, match="shared budget"):
        result.payload()
    retained["private"]["capture"]["statements"][0]["parameters"] = [str(parameters[0])]
    assert json_digest(retained["private"], budget=metadata.budget) == public["private_digest"]
    assert json_digest({"original": ["text", 1, None]}, budget=metadata.budget) == canonical_digest(
        {"original": ["text", 1, None]}
    )
    # Compatibility replay alone deliberately does not certify original types.
    assert (
        replay_original(retained, inventory, budget=metadata.budget).payload(budget=metadata.budget)
        == public
    )


def rewrite_typed_diagnostic_parameter(child):
    """Test mutation of real private bytes, recomputing outer file commitments."""
    import json
    from hashlib import sha256

    from scripts.execution_capacity.attempt import digest, encode

    root = child / "c2c-originals"
    manifest = json.loads((root / "manifest.json").read_bytes())
    changed = 0
    for shard in manifest["shards"]:
        path = root / f"{shard['ordinal']:06}.jsonl"
        frames = [json.loads(line) for line in path.read_bytes().splitlines()]
        for frame in frames:
            if frame.get("type") == "uuid" and frame.get("value") == str(UUID(int=99)):
                frame["type"] = "scalar"
                changed += 1
        raw = b"".join(encode(frame) + b"\n" for frame in frames)
        path.write_bytes(raw)
        shard["bytes"] = len(raw)
        shard["sha256"] = sha256(raw).hexdigest()
    assert changed == 1
    raw = encode(manifest)
    (root / "manifest.json").write_bytes(raw)
    ledger_path = child / "attempt.jsonl"
    lines = ledger_path.read_bytes().splitlines(keepends=True)
    final = json.loads(lines[-1])
    assert final["kind"] == "c2c-private-final"
    final["body"]["manifest"] = {"size_bytes": len(raw), "sha256": sha256(raw).hexdigest()}
    del final["digest"]
    final["digest"] = digest(final)
    lines[-1] = encode(final) + b"\n"
    ledger_path.write_bytes(b"".join(lines))


def test_actual_diagnostic_original_streams_one_query_before_committed_finish(
    tmp_path, monkeypatch
):
    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        acquire_round,
        seal_acquired,
    )

    with monkeypatch.context() as first:
        value = asyncio.run(acquire_final(tmp_path, first))
    base = seal_acquired(tmp_path, monkeypatch, value)
    with monkeypatch.context() as second:
        _round_value, location = asyncio.run(
            acquire_round(tmp_path, second, base, value, diagnostic=True)
        )
        context = OfflineProofContext(
            bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
            rounds=[location],
            signing_secret=value.secret,
            cursor_secret=value.cursor,
            budget=value.budget,
        )

        class Sink:
            def __init__(self):
                self.events = []
                self.query_value = None
                self.committed = None

            def base(self, seal, unit):
                self.events.append(("base", seal.seal_id, unit.kind))

            def query(self, value):
                self.query_value = value
                self.events.append(("query", value.sample_id))

            def round(self, value):
                self.events.append(("round", value.origin.round.round_id))

            def origin(self, value):
                self.events.append(("origin", value.round_id))

            def unit(self, value):
                self.events.append(("unit", value.kind))

            def finish(self, count, digest):
                self.committed = count, digest

        sink = Sink()
        context.stream_projection(sink)
        assert [event[0] for event in sink.events] == [
            "base",
            "query",
            "round",
            "origin",
            "unit",
        ]
        assert sink.query_value.statements
        assert sink.committed[0] == 1
        assert len(sink.committed[1]) == 64

        class FailingSink(Sink):
            def query(self, value):
                super().query(value)
                raise ValueError("sink failed before publication")

        failed = FailingSink()
        with pytest.raises(PrivateProofError, match="original replay failed"):
            context.stream_projection(failed)
        assert [event[0] for event in failed.events] == ["base", "query"]
        assert failed.committed is None


def test_actual_diagnostic_original_stream_emits_finite_role_shards(tmp_path, monkeypatch):
    from api.tests.scripts.capacity_population_private import OriginalProjectionShards
    from scripts.acceptance.capacity_c2c_models import C2c
    from scripts.acceptance.capacity_io import read_relative, strict_json
    from scripts.acceptance.capacity_models import Cleanup, Diagnostics, Seal
    from scripts.execution_capacity.offline_context import BaseLocation, OfflineProofContext
    from scripts.execution_capacity.test_final_connection_fixture import (
        acquire_final,
        acquire_round,
        seal_acquired,
    )

    with monkeypatch.context() as first:
        value = asyncio.run(acquire_final(tmp_path, first))
    base = seal_acquired(tmp_path, monkeypatch, value)
    with monkeypatch.context() as second:
        _round_value, location = asyncio.run(
            acquire_round(tmp_path, second, base, value, diagnostic=True)
        )
        context = OfflineProofContext(
            bases=[BaseLocation(base.ledger_root, base.retained_root, base.image_path)],
            rounds=[location],
            signing_secret=value.secret,
            cursor_secret=value.cursor,
            budget=value.budget,
        )
        root = tmp_path / "public-shards"
        root.mkdir(mode=0o700)
        sink = OriginalProjectionShards(root)
        try:
            context.stream_projection(sink)
            assert sink.finished
            shards = {}
            for item in sink.descriptors:
                shard = strict_json(read_relative(root, item["path"], item["size_bytes"]))
                shards.setdefault(item["role"], []).append(shard)
            assert {"seal", "c2c", "diagnostics", "cleanup"} == set(shards)
            assert Seal.model_validate(shards["seal"][0]).cohorts
            assert sum(len(row["units"]) for row in shards["c2c"]) == 2
            assert sum(len(row["queries"]) for row in shards["diagnostics"]) == 1
            assert sum(len(row["rounds"]) for row in shards["cleanup"]) == 1
            assert sum(len(row["cohorts"]) for row in shards["cleanup"]) >= 2
            for row in shards["c2c"]:
                C2c.model_validate(row)
            for row in shards["diagnostics"]:
                Diagnostics.model_validate(row)
            for row in shards["cleanup"]:
                Cleanup.model_validate(row)
        finally:
            sink.close()
