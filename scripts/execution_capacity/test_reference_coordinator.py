"""Offline actual authority joins; all operating-system effects are substituted."""

import pytest
from scripts.acceptance import capacity_models as models


@pytest.fixture(autouse=True)
def host_clock_boundary(monkeypatch):
    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "offline-boot",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )


def window_plan(**changes):
    return {
        "window_id": "window",
        "startup_ns": 10_000_000_000,
        "marker_count": 81,
        "marker_anchor": "first-minimal-ready-receipt",
        "trigger": "guest-open-receipt",
        "seconds": 30,
        "session_ids": [f"s{i}" for i in range(10)],
        "calibration_window": {"offset_ns": 0, "budget_ns": 15_000_000_000},
        "measurement": {
            "start_offset_ns": 0,
            "end_offset_ns": 27_000_000_000,
            "control_margin_ns": 1_000_000_000,
        },
        **changes,
    }


def test_immutable_measurement_retains_full_original_window():
    plan = models.WindowPlan.model_validate(window_plan())
    assert plan.seconds == 30
    assert plan.measurement.phase(26_999_999_999) == "measured"
    assert plan.measurement.phase(27_000_000_000) == "tail"
    assert plan.measurement.phase(-1) == "setup"


@pytest.mark.parametrize(
    ("end", "margin"), [(28_000_000_001, 1), (27_000_000_001, 1_000_000_000), (0, 1)]
)
def test_infeasible_measurement_guard_is_rejected_before_effects(end, margin):
    value = window_plan(
        measurement={"start_offset_ns": 0, "end_offset_ns": end, "control_margin_ns": margin}
    )
    with pytest.raises(ValueError, match="measurement"):
        models.WindowPlan.model_validate(value)


def test_calibration_schedule_counts_full_connect_auth_and_control_intervals():
    from scripts.execution_capacity.reference_schedule import CalibrationSchedule

    rule = {
        "connect_auth_ns": 100_000_000,
        "echo_rtt_ns": 100_000_000,
        "transfer_floor_bps": 18_000_000,
        "process_control_ns": 1_000_000_000,
        "window_offset_ns": 0,
        "phase_budget_ns": 15_000_000_000,
        "boot_timeout_ns": 60_000_000_000,
        "source_timeout_ns": 180_000_000_000,
    }
    schedule = CalibrationSchedule.model_validate(rule)
    schedule.check_window(models.WindowPlan.model_validate(window_plan()))
    assert schedule.minimum_phase_ns() > 10_000_000_000
    with pytest.raises(ValueError, match="calibration"):
        CalibrationSchedule.model_validate({**rule, "phase_budget_ns": 8_000_000_000})


def test_incremental_feed_requires_committed_public_sql_join_and_keeps_tail(tmp_path):
    from types import SimpleNamespace

    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.source_export import progress_page, publish_progress
    from scripts.execution_capacity.test_source_timing import progress_fact

    state = SimpleNamespace(
        identity={"window_id": "window", "boot_id": "boot"}, verify=lambda: None
    )
    plan = models.WindowPlan.model_validate(window_plan())
    (tmp_path / "feed").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "feed") as journal:
        state.journal = journal
        raw = progress_fact()
        raw.update(
            ack=True,
            error=None,
            effective=True,
            public=True,
            after_ns=100,
            before_ns=90,
            marker_captured_ns=80,
        )
        publish_progress(
            state, [raw], plan.measurement, start_ns=100, query_before_ns=110, query_after_ns=120
        )
        first = progress_page(state, 0)
        assert first.rows[0].progress.phase == "measured"
        assert first.rows[0].query_after_ns == 120
        assert first.next_cursor == 1
        raw.update(
            event_id="tail",
            source_identity="tail",
            public_event_id="tail",
            after_ns=27_000_000_100,
            before_ns=27_000_000_090,
        )
        publish_progress(
            state,
            [raw],
            plan.measurement,
            start_ns=100,
            query_before_ns=28_000_000_000,
            query_after_ns=28_000_000_100,
        )
        assert progress_page(state, 1).rows[0].progress.phase == "tail"
        raw.update(event_id="unjoined", effective=False)
        with pytest.raises(ValueError, match="persisted"):
            publish_progress(
                state,
                [raw],
                plan.measurement,
                start_ns=100,
                query_before_ns=28_000_000_000,
                query_after_ns=28_000_000_100,
            )
        assert progress_page(state, 2).rows == []
        assert progress_page(state, 0).rows[0] == first.rows[0]


def test_live_workload_publishes_actual_join_before_final_settlement(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.source_export import progress_page
    from scripts.execution_capacity.test_source_timing import progress_fact

    raw = progress_fact()
    raw.update(ack=True, error=None, effective=True, public=True)
    plan = models.WindowPlan.model_validate(window_plan())

    class Facts:
        async def progress(self, rows):
            return [{**r, "effective": True, "public": True} for r in rows]

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        workload = object.__new__(LiveWorkload)
        workload.bridge = SimpleNamespace(
            identity={"window_id": "window", "boot_id": "boot"},
            journal=journal,
            verify=lambda: None,
        )
        workload.facts = Facts()
        workload.progress_rows = lambda: [raw]
        workload.measurement = plan.measurement
        workload.source_start_ns = 0
        asyncio.run(workload.publish_incremental())
        assert progress_page(workload.bridge, 0).rows[0].progress.event_id == raw["event_id"]
        assert journal.get("live_window", "window") is None
        raw["ack"] = False
        raw["event_id"] = "uncommitted"
        asyncio.run(workload.publish_incremental())
        assert progress_page(workload.bridge, 1).rows == []


def test_host_incremental_ack_uses_first_durable_receipt_across_reopen(tmp_path, monkeypatch):
    from scripts.execution_capacity.attempt import AttemptLedger, digest
    from scripts.execution_capacity.reference_session import GuestSession
    from scripts.execution_capacity.source_export import export_progress
    from scripts.execution_capacity.test_source_timing import progress_fact

    identity = {"window_id": "window", "boot_id": "boot"}
    progress = export_progress(progress_fact(), window_id="window", phase="measured")
    joined = {"progress": progress.model_dump(), "query_before_ns": 5, "query_after_ns": 6}
    rows = [joined]
    page = dict(
        schema_version=3,
        **identity,
        cursor=0,
        next_cursor=1,
        total=1,
        rows=rows,
        digest=digest(rows),
    )
    with AttemptLedger.create(tmp_path / "host", {}) as ledger:
        ledger.append(
            "guest-control-intent",
            {
                "command_id": "c",
                "action": "progress",
                "request": {"identity": identity, "cursor": 0},
            },
        )
        ledger.append(
            "guest-status-poll-outcome",
            {
                "command_id": "c",
                "owner": "short",
                "state": "terminal-valid",
                "host_ns": 123,
                "response": {"identity": identity, "progress_page": page},
            },
        )
        session = object.__new__(GuestSession)
        session.identity, session.ledger, session.lock = identity, ledger, ledger.control_lock
        session._recover_progress_receipts()
        assert ledger.records("guest-progress-page")[0]["body"]["host_ns"] == 123
    with AttemptLedger.open(tmp_path / "host", {}) as ledger:
        session.ledger, session.lock = ledger, ledger.control_lock
        session._recover_progress_receipts()
        assert len(ledger.records("guest-progress-page")) == 1
        assert session.progress_cursor() == 1


def test_measured_updates_require_actual_incremental_ack_and_tail_stays_raw():
    from types import SimpleNamespace

    from scripts.acceptance.capacity_timing import validate_progress_receipts
    from scripts.execution_capacity.attempt import digest
    from scripts.execution_capacity.source_export import export_progress
    from scripts.execution_capacity.test_source_timing import progress_fact

    raw = {**progress_fact(), "ack": True, "error": None}
    measured = export_progress(raw, window_id="window", phase="measured")
    tail = measured.model_copy(update={"progress_id": "tail", "phase": "tail"})
    ack = SimpleNamespace(
        progress_id=measured.progress_id,
        progress_digest=digest(measured.model_dump()),
        clock_id="host",
        received_ns=50,
        query_before_ns=5,
        query_after_ns=6,
    )
    paint = SimpleNamespace(
        progress_id=measured.progress_id,
        event_id=measured.event_id,
        run_id=measured.run_id,
        sequence=measured.sequence,
        marker_id=measured.marker_id,
        projection_revision=measured.projection_revision,
        clock_id="host",
        context_id="context",
        visible=True,
        source_ack_received_ns=50,
        public_readback_received_ns=20,
        paint_received_ns=30,
    )
    window = SimpleNamespace(
        host_ready_sent_ns=10,
        coordinator_start_ns=40,
        coordinator_end_ns=60,
        session_ids=["session"],
        context_ids=["context"],
        claims=[SimpleNamespace(run_id=measured.run_id, session_id="session")],
    )
    progress = {p.progress_id: p for p in [measured, tail]}
    # Observer was ready before guest start; this true postcommit paint arrived
    # before the later host open receipt. Triggered actions retain their gate.
    validate_progress_receipts(
        progress, {measured.progress_id: paint}, [ack], {"window": window}, "host"
    )
    with pytest.raises(ValueError, match="incremental"):
        validate_progress_receipts(
            progress, {measured.progress_id: paint}, [], {"window": window}, "host"
        )
    with pytest.raises(ValueError, match="paint"):
        validate_progress_receipts(progress, {}, [ack], {"window": window}, "host")
    assert "tail" in progress

    paint.event_id = "wrong"
    with pytest.raises(ValueError, match="identity"):
        validate_progress_receipts(
            progress, {measured.progress_id: paint}, [ack], {"window": window}, "host"
        )


def test_vm_accepts_only_journaled_observed_cgroup_transition(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_vm as module
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_vm import ColdVM

    original = {
        "pid": 14,
        "start_ticks": 81,
        "argv_digest": "a",
        "executable_sha256": "b",
        "executable_device": 1,
        "executable_inode": 2,
        "cgroup": "0::/old",
        "boot_id": "boot",
    }
    current = {**original, "cgroup": "0::/owned"}
    monkeypatch.setattr(module, "process_identity", lambda pid: dict(current))
    with AttemptLedger.create(tmp_path / "vm", {}) as ledger:
        vm = ColdVM(SimpleNamespace(uuid="uuid"), ledger, sample_id="s", window_id="w")
        vm.identity, vm.process = original, SimpleNamespace(pid=14)
        allocation = {"identity": {k: v for k, v in current.items() if k != "boot_id"}}
        with pytest.raises(ValueError, match="resource"):
            vm.accept_resource_transition(allocation)
        ledger.append("resource-process", allocation)
        vm.accept_resource_transition(allocation)
        assert vm.identity == current
        assert ledger.records("qemu-resource-transition")[0]["body"]["before"] == original
        current["executable_inode"] = 99
        with pytest.raises(ValueError, match="identity"):
            vm.accept_resource_transition(allocation)


def test_vm_socket_connection_uses_owned_peer_and_records_socket_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_vm as module
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_vm import ColdVM

    original = {"pid": 14, "cgroup": "owned"}
    channel = SimpleNamespace(wire=SimpleNamespace(close=lambda: None))
    calls = []
    monkeypatch.setattr(module, "process_identity", lambda pid: original)
    monkeypatch.setattr(module, "socket_identity", lambda path: {"device": 2, "inode": 3, "uid": 4})
    monkeypatch.setattr(
        module, "connect_owned", lambda path, **kwargs: calls.append(kwargs) or channel
    )
    with AttemptLedger.create(tmp_path / "vm", {}) as ledger:
        vm = ColdVM(
            SimpleNamespace(uuid="uuid", qmp_socket=tmp_path / "qmp"),
            ledger,
            sample_id="s",
            window_id="w",
        )
        vm.identity, vm.process, vm.pidfd = original, SimpleNamespace(pid=14), 77
        assert vm.open_channel("qmp", timeout=1) is channel
        assert calls == [{"pid": 14, "uid": 4, "device": 2, "inode": 3, "timeout": 1}]
        assert ledger.records("qemu-socket-observed")[0]["body"]["identity"]["inode"] == 3


def test_failed_stage_is_consumed_and_never_retried_after_reopen(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import StageLedger

    effects = []
    with AttemptLedger.create(tmp_path / "stages", {}) as ledger:
        stages = StageLedger(ledger, "window")

        def uncertain():
            effects.append("effect")
            raise EOFError("response lost")

        with pytest.raises(EOFError):
            stages.run("boot", (), uncertain)
        assert ledger.records("lifecycle-failure")[0]["body"]["disposition"] == "retained"
    with (
        AttemptLedger.open(tmp_path / "stages", {}) as ledger,
        pytest.raises(ValueError, match="retained"),
    ):
        StageLedger(ledger, "window").run("boot", (), uncertain)
    assert effects == ["effect"]


def test_stage_predecessor_is_mandatory_and_intent_precedes_effect(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import StageLedger

    with AttemptLedger.create(tmp_path / "stages", {}) as ledger:
        stages = StageLedger(ledger, "window")
        with pytest.raises(ValueError, match="predecessor"):
            stages.run("resume", ("bind",), lambda: None)

        def effect():
            assert ledger.records("lifecycle-intent")[-1]["body"]["stage"] == "bind"
            return {"observation": "actual"}

        assert stages.run("bind", (), effect) == {"observation": "actual"}
        stages.run("resume", ("bind",), lambda: None)
        with pytest.raises(ValueError, match="consumed"):
            stages.run("resume", ("bind",), lambda: None)


def test_boot_coordinator_preserves_fixed_physical_stage_order(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_lifecycle as module
    from scripts.execution_capacity.attempt import AttemptLedger

    observed = []

    def event(name, value=None):
        def effect(*args, **kwargs):
            observed.append(name)
            return value

        return effect

    with AttemptLedger.create(
        tmp_path / "coordinator",
        {
            "window_plans": {"window": window_plan()},
            "calibration_schedule": {
                "connect_auth_ns": 100_000_000,
                "echo_rtt_ns": 100_000_000,
                "transfer_floor_bps": 18_000_000,
                "process_control_ns": 1_000_000_000,
                "window_offset_ns": 0,
                "phase_budget_ns": 15_000_000_000,
                "boot_timeout_ns": 60_000_000_000,
                "source_timeout_ns": 180_000_000_000,
            },
            "guest_control": {"command_timeout_ns": 2_000_000_000},
            "calibration": {"phases": ["baseline", "pre", "window", "post"]},
        },
    ) as ledger:
        monkeypatch.setattr(ledger, "bind_clock", lambda: "clock")
        qmp = SimpleNamespace(negotiate_qmp=event("qmp-negotiate"))
        qga = object()
        vm = SimpleNamespace(
            ledger=ledger,
            window_id="window",
            sample_id="sample",
            plan=SimpleNamespace(uuid="vm"),
            create_overlay=event("overlay"),
            launch=event("launch"),
            open_channel=lambda kind, **kw: qmp if kind == "qmp" else qga,
            verify_paused=event("paused"),
            resume=event("resume"),
        )
        network = SimpleNamespace(
            ledger=ledger,
            plan=SimpleNamespace(attempt_id="round"),
            preflight=event("net-preflight"),
            create=event("net-create"),
            reserve_consumer=event("reserve"),
            shape=event("shape"),
        )
        resources = SimpleNamespace(
            ledger=ledger, configure=event("resources"), bind_vm=event("bind")
        )
        session = SimpleNamespace(infrastructure=lambda phase: observed.append("guest-" + phase))
        monkeypatch.setattr(
            module, "GuestAgent", lambda channel: SimpleNamespace(synchronize=event("qga-sync"))
        )
        monkeypatch.setattr(
            module, "GuestSession", SimpleNamespace(discover=event("discover", session))
        )
        monkeypatch.setattr(
            module,
            "Calibration",
            lambda *args: SimpleNamespace(run=lambda phase, **kw: observed.append(phase)),
        )
        monkeypatch.setattr(
            module,
            "verify_round",
            lambda *args: SimpleNamespace(round_id="round", window_id="window", sample_id="sample"),
        )
        monkeypatch.setattr(module, "vm_plan_record", lambda plan: {})
        ledger.plan["vm"] = {}
        controller = module.ReferenceAttempt(
            vm,
            network,
            resources,
            {
                "nonce": "00000000-0000-0000-0000-000000000001",
                "attempt_id": "round",
                "window_id": "window",
                "sample_id": "sample",
            },
            parent=SimpleNamespace(
                plan={
                    "samples": [
                        {
                            "sample_id": "sample",
                            "dimension": "standard",
                            "mode": "warm",
                            "operation": "first_screen",
                            "ordinal": 0,
                            "target": {
                                "scope_id": "scope",
                                "run_id": "run",
                                "public_id": "public",
                                "revision": "1",
                                "step_id": None,
                            },
                            "window_id": "window",
                            "reset_id": None,
                            "physical_window_id": "window",
                            "prewarm_completed_ns": 1,
                            "action_id": "action",
                            "page_id": "page",
                            "context_id": "context",
                        }
                    ]
                }
            ),
        )
        controller.boot()
        assert observed == [
            "resources",
            "net-preflight",
            "net-create",
            "overlay",
            "reserve",
            "launch",
            "qmp-negotiate",
            "paused",
            "bind",
            "resume",
            "qga-sync",
            "discover",
            "guest-resources",
            "guest-start",
            "baseline",
            "shape",
            "pre",
        ]
        with pytest.raises(ValueError, match="consumed"):
            controller.boot()


def test_failure_exit_only_signals_exact_pidfd_and_is_never_clean(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_vm as module
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_vm import ColdVM

    sent = []
    current = {"pid": 14, "start_ticks": 1, "cgroup": "owned"}
    monkeypatch.setattr(module, "process_identity", lambda pid: current)
    monkeypatch.setattr(
        module.signal, "pidfd_send_signal", lambda fd, sig: sent.append((fd, sig)), raising=False
    )
    with AttemptLedger.create(tmp_path / "exit", {}) as ledger:
        vm = ColdVM(
            SimpleNamespace(uuid="vm", overlay=tmp_path / "overlay"),
            ledger,
            sample_id="sample",
            window_id="window",
        )
        vm.process, vm.pidfd, vm.identity = SimpleNamespace(pid=14), 123, dict(current)
        monkeypatch.setattr(vm, "wait_exit", lambda seconds: -15)
        vm.force_exit()
        assert sent == [(123, module.signal.SIGTERM)]
        assert ledger.records("qemu-forced-exit")[0]["body"]["clean"] is False
        with pytest.raises(ValueError, match="consumed"):
            vm.force_exit()
        assert len(sent) == 1


def test_parent_round_binding_reserves_global_sample_before_child_and_rejects_replacement(tmp_path):
    from uuid import uuid4

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_round import reserve_round, verify_round

    parent_id, round_id = str(uuid4()), str(uuid4())
    parent_plan = {
        "attempt_id": parent_id,
        "samples": [{"sample_id": "s", "window_id": "w", "physical_window_id": "w"}],
    }
    child_plan = {
        "round": {
            "parent_attempt_id": parent_id,
            "round_id": round_id,
            "sample_id": "s",
            "window_id": "w",
        }
    }
    with AttemptLedger.create(tmp_path / "parent", parent_plan) as parent:
        target = parent.root / "rounds" / round_id
        binding = reserve_round(parent, child_plan, target, seal_digest="a" * 64)
        assert not target.exists()
        with pytest.raises(ValueError, match=r"consumed|already"):
            reserve_round(parent, child_plan, target, seal_digest="a" * 64)
        target.parent.mkdir(mode=0o700)
        with AttemptLedger.create(target, child_plan) as child:
            assert verify_round(parent, child) == binding
            child.plan["round"]["parent_attempt_id"] = str(uuid4())
            with pytest.raises(ValueError, match=r"parent|round"):
                verify_round(parent, child)


def test_source_start_anchors_markers_to_observed_metadata_then_returns_cohort(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    seen = []
    with AttemptLedger.create(tmp_path / "start", {}) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "window", "stage": "boot"})
        controller = object.__new__(ReferenceAttempt)
        controller.ledger = ledger
        controller.vm = SimpleNamespace(window_id="window")
        controller.stages = StageLedger(ledger, "window")
        controller.schedule = SimpleNamespace(boot_timeout_ns=1_000_000_000)
        controller.marker_thread = None
        controller.thread_errors = []
        controller.session = SimpleNamespace(
            start=lambda: seen.append("start"),
            status=lambda: {
                "minimal_ready": {"guest_ns": 1},
                "cohort": {"cohort_digest": "a" * 64},
            },
            run_markers=lambda: seen.append("markers"),
        )
        cohort = controller.start_source()
        controller.marker_thread.join()
        assert seen == ["start", "markers"]
        assert cohort["cohort_digest"] == "a" * 64
        with pytest.raises(ValueError, match="consumed"):
            controller.start_source()


def test_native_ready_requires_registered_owned_client_and_records_actual_host_time(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    with AttemptLedger.create(
        tmp_path / "native", {"native_contexts": [{"context_id": "c", "session_id": "s"}]}
    ) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "cohort"})
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id="w")
        controller.stages, controller.native = StageLedger(ledger, "w"), {}
        controller.ready_observations = {}
        observation = {
            "consumer_id": "browser",
            "context_id": "c",
            "session_id": "s",
            "page_id": "p",
            "subscription_id": "subscription",
            "event_digest": "a" * 64,
        }
        with pytest.raises(ValueError, match="registered"):
            controller.observe_native_ready(observation)
        controller.native["browser"] = {"identity": {"pid": 1}}
        monkeypatch.setattr(controller, "_native_alive", lambda: None)
        monkeypatch.setattr(
            "scripts.execution_capacity.reference_lifecycle.time.monotonic_ns", lambda: 456
        )
        ready = controller.observe_native_ready(observation)
        assert ready["received_ns"] == 456
        assert ready["observation"]["subscription_id"] == "subscription"
        with pytest.raises(ValueError, match="consumed"):
            controller.observe_native_ready(observation)


def test_native_registration_cannot_precede_reservation_or_admit_wrong_argv(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_lifecycle as module
    from scripts.execution_capacity.attempt import AttemptLedger

    with AttemptLedger.create(
        tmp_path / "native",
        {"native_consumers": {"browser": {"argv_digest": "a", "executable_sha256": "b"}}},
    ) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "boot"})
        controller = object.__new__(module.ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id="w")
        controller.stages, controller.native = module.StageLedger(ledger, "w"), {}
        seen = []
        controller.network = SimpleNamespace(
            reserve_consumer=lambda *a, **kw: seen.append("reserve")
        )
        with pytest.raises(ValueError, match="predecessor"):
            controller.register_native("browser", SimpleNamespace(pid=12))
        controller.reserve_native("browser")
        assert seen == ["reserve"]
        monkeypatch.setattr(
            module,
            "child_snapshot",
            lambda pid: {"pid": pid, "argv_digest": "wrong", "executable_sha256": "b"},
        )
        with pytest.raises(ValueError, match="argv"):
            controller.register_native("browser", SimpleNamespace(pid=12))
        assert controller.native == {}


def test_final_window_join_uses_guest_and_host_observations_without_epoch_math(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_evidence import join_window

    identity = {"attempt_id": "round", "window_id": "window", "boot_id": "boot"}
    source = {
        "attempt_id": "round",
        "window_id": "window",
        "boot_id": "boot",
        "source_digest": "a" * 64,
        "minimal_ready_ns": 900,
        "start_ns": 10_000_000_900,
        "end_ns": 40_000_000_900,
        "cohort_ns": 1000,
        "ready_ns": 2000,
        "done_ns": 39_000_000_900,
        "measurement_closed_ns": 37_000_000_900,
        "cohort_digest": "b" * 64,
        "native_ready_digest": "c" * 64,
        "native_done_digest": "d" * 64,
        "session_ids": [],
        "claims": [],
        "batch_id": "batch",
        "suite_version": "suite",
        "batch_results": 5000,
        "cleanup": "pending_C",
        "measurement": window_plan()["measurement"],
    }
    with AttemptLedger.create(tmp_path / "join", {}) as ledger:
        for kind, body in [
            ("guest-metadata-anchor", {"host_ns": 100}),
            ("guest-cohort-received", {"host_ns": 200}),
            ("guest-window-open-received", {"host_ns": 500}),
            ("native-complete-observed", {"host_ns": 800}),
            ("guest-measurement-closed-received", {"host_ns": 750}),
        ]:
            ledger.append(kind, {"identity": identity, **body})
        for action, sent, received in [("client-ready", 300, 400), ("client-done", 850, 900)]:
            cid = action
            ledger.append(
                "guest-control-intent",
                {
                    "command_id": cid,
                    "action": action,
                    "host_ns": sent,
                    "request": {"identity": identity},
                },
            )
            ledger.append("guest-control-result", {"command_id": cid, "host_ns": received})
        binding = {
            "parent_attempt_id": "parent",
            "round_id": "round",
            "sample_id": "s",
            "window_id": "window",
            "parent_plan_digest": "a" * 64,
            "child_plan_digest": "b" * 64,
            "reservation_digest": "c" * 64,
            "schema_version": 1,
            "child_origin_sha256": "d" * 64,
        }
        window = join_window(ledger, identity, source, [], [], [], binding, "host-clock")
        assert window.coordinator_start_ns == 500
        assert window.guest_start_ns == 10_000_000_900
        assert window.round_origin.round_id == "round"
        assert window.host_done_received_ns == 900
        source["attempt_id"] = "wrong-round"
        with pytest.raises(ValueError, match="round"):
            join_window(ledger, identity, source, [], [], [], binding, "host-clock")


def test_measurement_close_is_guest_observed_and_does_not_stop_original_load(tmp_path, monkeypatch):
    from scripts.execution_capacity.guest_state import GuestState
    from scripts.execution_capacity.test_guest_state import identity

    tmp_path.chmod(0o700)
    monkeypatch.setattr(
        "scripts.execution_capacity.guest_state.process_snapshot", lambda pid: {"pid": pid}
    )
    clock = [15]
    monkeypatch.setattr(
        "scripts.execution_capacity.guest_state.time.monotonic_ns", lambda: clock[0]
    )
    state = GuestState(tmp_path, identity())
    try:
        state.initialize()
        state.publish(
            "measurement",
            {
                "rule": {"start_offset_ns": 0, "end_offset_ns": 10, "control_margin_ns": 1},
                "start_ns": 10,
                "end_ns": 40,
            },
        )
        assert state.close_measurement([]) is None
        clock[0] = 20
        assert state.close_measurement([])["guest_ns"] == 20
        assert state.read("measurement")["end_ns"] == 40
        clock[0] = 21
        assert state.close_measurement([])["guest_ns"] == 20
        assert state.read("client_done") is None
    finally:
        state.close()


def test_native_completion_rejects_success_flag_without_sending_done(tmp_path):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    with AttemptLedger.create(tmp_path / "complete", {}) as ledger:
        for stage in ("open", "calibration-window"):
            ledger.append("lifecycle-completed", {"window_id": "w", "stage": stage})
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id="w")
        controller.stages = StageLedger(ledger, "w")
        done = []
        controller.session = SimpleNamespace(done=lambda **kw: done.append(kw))
        with pytest.raises(ValueError, match="Measurements"):
            controller.client_done(True)
        assert not done
        assert ledger.records("lifecycle-failure")


def test_recovery_retains_unknown_vm_and_never_releases_network(tmp_path):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    with AttemptLedger.create(tmp_path / "recovery", {}) as ledger:
        controller = object.__new__(ReferenceAttempt)
        controller.ledger = ledger
        controller.stages = StageLedger(ledger, "w")

        def unknown():
            raise ValueError("process replaced")

        controller.vm = SimpleNamespace(window_id="w", force_exit=unknown)
        controller.session, controller.native = None, {}
        controller.marker_thread = controller.calibration_thread = None
        controller.qmp = controller.qga = None
        deleted = []
        controller.network = SimpleNamespace(cleanup=lambda: deleted.append(True))
        result = controller.recover_failure()
        assert result["clean"] is False
        assert result["uncertainties"] == ["vm:ValueError"]
        assert not deleted
        assert ledger.records("lifecycle-recovery-intent")
        with pytest.raises(ValueError, match="consumed"):
            controller.recover_failure()


def test_final_export_waits_for_exact_start_exit_and_keeps_clients_alive(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    calls = []
    with AttemptLedger.create(tmp_path / "final", {}) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "done"})
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id="w")
        controller.stages = StageLedger(ledger, "w")
        controller.schedule = SimpleNamespace(source_timeout_ns=1_000_000_000)
        controller.marker_thread = controller.calibration_thread = None
        controller.thread_errors = []
        polls = iter([False, True])

        def poll():
            result = next(polls)
            calls.append(("poll", result))
            return result

        def shards(kind):
            calls.append(("export", kind))
            raise ValueError("missing actual source")

        controller.session = SimpleNamespace(poll_start=poll, source_shards=shards)
        monkeypatch.setattr(controller, "_native_alive", lambda: calls.append(("native", "alive")))
        monkeypatch.setattr(
            controller, "_calibrate", lambda phase: calls.append(("calibration", phase))
        )
        monkeypatch.setattr(
            "scripts.execution_capacity.reference_lifecycle.time.sleep", lambda _: None
        )
        with pytest.raises(ValueError, match="missing actual source"):
            controller.finish_source()
        assert calls.index(("poll", True)) < calls.index(("export", "metadata"))
        assert calls.count(("native", "alive")) >= 2
        assert ledger.records("lifecycle-failure")


def test_controller_rejects_foreign_round_before_any_physical_effect(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_lifecycle as module
    from scripts.execution_capacity.attempt import AttemptLedger

    with AttemptLedger.create(tmp_path / "identity", {}) as ledger:
        binding = SimpleNamespace(round_id="owned", window_id="w", sample_id="s")
        monkeypatch.setattr(module, "verify_round", lambda *args: binding)
        vm = SimpleNamespace(ledger=ledger, window_id="w", sample_id="s")
        other = SimpleNamespace(ledger=ledger)
        with pytest.raises(ValueError, match="round"):
            module.ReferenceAttempt(
                vm,
                other,
                other,
                {"attempt_id": "foreign", "window_id": "w", "sample_id": "s"},
                parent=object(),
            )
        assert ledger.rows == []


def test_progress_observer_also_reads_guest_interval_closure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    with AttemptLedger.create(tmp_path / "poll", {}) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "source"})
        controller = object.__new__(ReferenceAttempt)
        controller.stages = StageLedger(ledger, "w")
        controller.thread_errors = []
        seen = []
        monkeypatch.setattr(controller, "_status", lambda: seen.append("status"))
        controller.session = SimpleNamespace(
            progress=lambda: seen.append("progress") or {"cursor": 1}
        )
        assert controller.observe_progress() == {"cursor": 1}
        assert seen == ["status", "progress"]


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "preopen_live",
        "preload_live",
        "late_live_capture",
        "future_live_capture",
        "future_resource",
        "missing_visual",
        "wrong_browser",
        "wrong_source",
        "duplicate_source",
        "empty_resource",
        "wrong_resource_clock",
        "preload_resource",
        "unclosed_measurement",
        "missing_paint",
    ],
)
def test_typed_actual_completion_sends_done_without_stopping_clients_or_source(
    tmp_path, monkeypatch, defect
):
    from types import SimpleNamespace

    from api.tests.scripts.capacity_synthetic import transcript
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    roles, _ = transcript()
    wid = "window-0"
    selected = next(
        p
        for p in roles["protocol"]["samples"]
        if p["window_id"] == wid
        and (
            p["operation"] == "live_visible"
            if defect
            in {"preopen_live", "preload_live", "late_live_capture", "future_live_capture"}
            else p["operation"] != "admission"
        )
    )
    sid = selected["sample_id"]
    measured = {
        p["progress_id"]: p
        for p in roles["workload"]["progress"]
        if p["window_id"] == wid and p["phase"] == "measured"
    }
    acks = {a["progress_id"]: a for a in roles["workload"]["source_acks"]}
    observations = {k: v for k, v in roles["measurements"].items() if not isinstance(v, list)}
    for key in ("samples", "sources", "browsers", "resources"):
        observations[key] = [r for r in roles["measurements"][key] if r["sample_id"] == sid]
    observations["live_paints"] = [
        p for p in roles["measurements"]["live_paints"] if p["progress_id"] in measured
    ]
    observations["markers"] = []
    observations["errors"] = []
    source_window = roles["workload"]["windows"][0]
    if defect == "preopen_live":
        source_window["coordinator_start_ns"] += 2_000_000
        assert observations["samples"][0]["end_ns"] < source_window["coordinator_start_ns"]
    contexts = [
        {"context_id": c, "session_id": s}
        for c, s in zip(source_window["context_ids"], source_window["session_ids"], strict=True)
    ]
    identity = {"window_id": wid, "boot_id": "boot-0", "attempt_id": "round"}
    with AttemptLedger.create(
        tmp_path / "valid-complete",
        {"protocol_id": "synthetic-protocol", "native_contexts": contexts},
    ) as ledger:
        for stage in ("open", "calibration-window"):
            ledger.append("lifecycle-completed", {"window_id": wid, "stage": stage})
        ledger.append(
            "guest-measurement-closed-received",
            {
                "identity": identity,
                "host_ns": 47_000_000_000,
                "observation": {"feed_cursor": len(measured)},
            },
        )
        ledger.append(
            "guest-control-intent",
            {
                "command_id": "ready",
                "action": "client-ready",
                "request": {"identity": identity},
                "host_ns": 18_000_000_000,
            },
        )
        ledger.append("guest-control-result", {"command_id": "ready", "host_ns": 18_000_000_001})
        for key, p in measured.items():
            ack = acks[key]
            ledger.append(
                "guest-progress-page",
                {
                    "identity": identity,
                    "host_ns": ack["received_ns"],
                    "page": {
                        "rows": [
                            {
                                "progress": p,
                                "query_before_ns": ack["query_before_ns"],
                                "query_after_ns": ack["query_after_ns"],
                            }
                        ]
                    },
                },
            )
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id=wid, sample_id=sid)
        controller.stages = StageLedger(ledger, wid)
        controller.clock_id = "host-clock"
        controller.thread_errors = []
        controller.cohort = {
            "sessions": {c["session_id"]: c["run_id"] for c in source_window["claims"]}
        }
        from scripts.execution_capacity.reference_round import RoundBinding

        controller.round_binding = RoundBinding.model_validate(
            {
                **{
                    k: v
                    for k, v in source_window["round_origin"].items()
                    if k not in {"schema_version", "child_origin_sha256"}
                },
                "child_path": "/private/fixture/round",
            }
        )
        controller.sample_plan = models.Plan.model_validate(selected)
        ledger.append(
            "guest-window-open-received",
            {"identity": identity, "host_ns": source_window["coordinator_start_ns"]},
        )
        done = []
        controller.session = SimpleNamespace(
            identity=identity,
            progress_cursor=lambda: len(measured),
            done=lambda **kw: done.append(kw),
        )
        alive = []
        monkeypatch.setattr(controller, "_native_alive", lambda: alive.append(True))
        monkeypatch.setattr(
            "scripts.execution_capacity.reference_lifecycle.time.monotonic_ns",
            lambda: 48_000_000_000,
        )
        if defect == "preload_live":
            observations["resources"][0]["start_ns"] = 18_000_000_000
        elif defect == "late_live_capture":
            observations["resources"][0]["start_ns"] = 21_000_000_001
        elif defect == "future_live_capture":
            observations["resources"][0]["end_ns"] = 48_000_000_001
        elif defect == "future_resource":
            observations["resources"][0]["end_ns"] = 50_000_000_000
            observations["resources"][0]["frames"][-1]["observed_ns"] = 49_000_000_000
        elif defect == "missing_visual":
            observations["samples"][0]["browser_id"] = None
            observations["browsers"] = observations["resources"] = []
        elif defect == "wrong_browser":
            observations["browsers"][0]["action_id"] = "foreign-action"
        elif defect == "wrong_source":
            observations["sources"][0]["source_id"] = "foreign-source"
        elif defect == "duplicate_source":
            observations["sources"] *= 2
        elif defect == "empty_resource":
            observations["resources"][0]["frames"] = []
        elif defect == "wrong_resource_clock":
            observations["resources"][0]["clock_id"] = "foreign-clock"
        elif defect == "preload_resource":
            observations["resources"][0]["start_ns"] = 1
        elif defect == "unclosed_measurement":
            ledger.by_kind["guest-measurement-closed-received"].clear()
        elif defect == "missing_paint":
            observations["live_paints"].pop(0)
        if defect and defect != "preopen_live":
            with pytest.raises(
                ValueError, match=r"completion|resource|duplicate|measurement|paint"
            ):
                controller.client_done(observations)
            assert not done
            assert not ledger.records("native-complete-observed")
            return
        controller.client_done(observations)
        assert len(done) == len(alive) == 1
        assert done[0]["native_digest"] == controller.native_done_digest
        assert ledger.records("native-complete-observed")[0]["body"]["host_ns"] == 48_000_000_000
        assert not ledger.records("native-stop-intent")
        assert not ledger.records("qemu-force-exit-intent")


def test_all_fixed_calibration_phases_are_required_before_effects():
    from scripts.execution_capacity.reference_schedule import validate_phases

    for phases in (["baseline", "pre", "window"], ["baseline", "pre", "window", "post", "post"]):
        with pytest.raises(ValueError, match="phases"):
            validate_phases(phases)
    validate_phases(["baseline", "pre", "window", "post"])
