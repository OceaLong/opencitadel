"""Offline regressions for C2b3b review findings; no external effects."""

import asyncio

import pytest


def test_measurement_closure_waits_for_durable_delayed_public_join(tmp_path, monkeypatch):
    from scripts.acceptance.capacity_models import WindowPlan
    from scripts.execution_capacity.guest_state import GuestState
    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.source_export import progress_page
    from scripts.execution_capacity.test_guest_state import identity
    from scripts.execution_capacity.test_reference_coordinator import window_plan
    from scripts.execution_capacity.test_source_timing import progress_fact

    monkeypatch.setattr(
        "scripts.execution_capacity.guest_state.process_snapshot", lambda pid: {"pid": pid}
    )
    monkeypatch.setattr("time.monotonic_ns", lambda: 28_000_000_000)
    tmp_path.chmod(0o700)
    state = GuestState(tmp_path, identity())
    state.initialize()
    rule = WindowPlan.model_validate(window_plan()).measurement
    state.publish("measurement", {"start_ns": 0, "rule": rule.model_dump()})
    raw = progress_fact()
    raw.update(
        boot_id=state.identity["boot_id"],
        ack=True,
        error=None,
        before_ns=26_000_000_000,
        after_ns=26_000_000_001,
    )

    class Facts:
        visible = False

        async def progress(self, rows):
            return [{**r, "effective": self.visible, "public": self.visible} for r in rows]

    workload = object.__new__(LiveWorkload)
    workload.bridge, workload.facts = state, Facts()
    workload.measurement, workload.source_start_ns = rule, 0
    workload.progress_rows = lambda: [raw]
    try:
        raw["ack"] = False
        raw["error"] = "missing-persistence-receipt"
        asyncio.run(workload.publish_incremental())
        workload.close_measurement()
        assert state.read("measurement_closed") is None
        raw["ack"], raw["error"] = True, None
        asyncio.run(workload.publish_incremental())
        workload.close_measurement()
        assert state.read("measurement_closed") is None
        assert progress_page(state, 0).rows == []
        workload.facts.visible = True
        asyncio.run(workload.publish_incremental())
        workload.close_measurement()
        assert state.read("measurement_closed")["feed_cursor"] == 1
        assert progress_page(state, 0).rows[0].progress.event_id == raw["event_id"]
    finally:
        state.close()


@pytest.mark.parametrize("dispatch_ns", [50, 131])
def test_window_calibration_uses_first_open_and_keeps_fixed_deadline(
    tmp_path, monkeypatch, dispatch_ns
):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt, StageLedger

    now = [dispatch_ns]
    monkeypatch.setattr("time.monotonic_ns", lambda: now[0])
    identity = {"window_id": "w", "boot_id": "boot"}
    with AttemptLedger.create(tmp_path / "anchor", {}) as ledger:
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "ready"})
        ledger.append("lifecycle-completed", {"window_id": "w", "stage": "source"})
        ledger.append("guest-window-open-received", {"identity": identity, "host_ns": 10})
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.vm = ledger, SimpleNamespace(window_id="w")
        controller.stages = StageLedger(ledger, "w")
        controller.schedule = SimpleNamespace(
            boot_timeout_ns=1000, window_offset_ns=20, phase_budget_ns=100
        )
        controller.session = SimpleNamespace(identity=identity, last_completion_ns=25)
        controller.thread_errors = []
        controller.clock_id = "clock"

        def status():
            # A marker finishes between STATUS and the coordinator's read.
            controller.session.last_completion_ns = 999
            return {"running": {"start_ns": 1}}

        controller._status = status
        calls = []

        def run(phase, **kwargs):
            calls.append((phase, kwargs))
            now[0] = max(now[0], 100)

        controller.calibration = SimpleNamespace(run=run)
        controller.open_window()
        controller.calibration_thread.join()
        assert controller.open_received_ns == 10
        if dispatch_ns > 130:
            assert not calls
            assert controller.thread_errors
            assert not any(
                r["body"]["stage"] == "calibration-window"
                for r in ledger.records("lifecycle-completed")
            )
        else:
            assert calls == [("window", {"deadline_ns": 130})]
            assert not controller.thread_errors
        interval = ledger.records("calibration-phase-interval")[0]["body"]
        assert interval["scheduled_ns"] == 30
        assert interval["deadline_ns"] == 130


def test_calibration_control_lock_delay_cannot_restart_phase_budget(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_calibration import Calibration

    now = [50]
    monkeypatch.setattr("time.monotonic_ns", lambda: now[0])

    @contextmanager
    def delayed_lock():
        now[0] = 131
        yield

    with AttemptLedger.create(tmp_path / "lock", {}) as ledger:
        controller = object.__new__(Calibration)
        controller.ledger = ledger
        controller.session = SimpleNamespace(identity={"window_id": "w"})
        ledger.control_lock = delayed_lock()
        with pytest.raises(TimeoutError, match="deadline"):
            controller.run("window", deadline_ns=130)
        assert not ledger.records("calibration-intent")


def test_shared_window_slot_is_immutable_and_feasible():
    from scripts.acceptance.capacity_models import WindowPlan
    from scripts.execution_capacity.test_reference_coordinator import window_plan

    plan = window_plan(calibration_window={"offset_ns": 1, "budget_ns": 15_000_000_000})
    assert WindowPlan.model_validate(plan).calibration_window.offset_ns == 1
    plan["calibration_window"]["offset_ns"] = 27_000_000_000
    with pytest.raises(ValueError, match="calibration"):
        WindowPlan.model_validate(plan)


def test_phase_export_retains_actual_clock_anchor_deadline_and_overrun(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_evidence import calibration_intervals

    with AttemptLedger.create(tmp_path / "phases", {}) as ledger:
        for phase in ("baseline", "pre", "window", "post"):
            ledger.append(
                "calibration-phase-interval",
                {
                    "window_id": "w",
                    "clock_id": "clock",
                    "phase": phase,
                    "scheduled_ns": 30,
                    "deadline_ns": 130,
                    "before_ns": 50,
                    "after_ns": 140,
                },
            )
        result = calibration_intervals(ledger, "w", "clock")
        assert len(result) == 4
        assert result[2].after_ns == 140
        assert result[2].deadline_ns == 130
        with pytest.raises(ValueError, match="clock"):
            calibration_intervals(ledger, "w", "foreign")


def test_calibration_expiry_before_payload_retains_exact_child_failure(tmp_path, monkeypatch):
    import io
    from hashlib import sha256
    from types import SimpleNamespace

    import scripts.execution_capacity.reference_calibration as mod
    from scripts.execution_capacity.attempt import AttemptLedger

    now = [50]
    monkeypatch.setattr(mod.time, "monotonic_ns", lambda: now[0])
    process = SimpleNamespace(pid=7, stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=None)
    process.poll = lambda: process.returncode

    def wait(**kwargs):
        process.returncode = -15

    process.wait = wait

    def forbidden_payload(*args, **kwargs):
        raise AssertionError("expired phase must not receive secret/start payload")

    process.communicate = forbidden_payload
    argv = (
        b"\0".join(v.encode() for v in ["/usr/bin/python3", "-I", str(mod.SCRIPT), "client"])
        + b"\0"
    )
    actual = {"executable_sha256": "a" * 64, "argv_digest": sha256(argv).hexdigest()}
    sent = []
    with AttemptLedger.create(tmp_path / "payload", {}) as ledger:
        probe = object.__new__(mod.Calibration)
        probe.ledger = ledger
        probe.config = {
            "phases": ["baseline"],
            "service_sha256": "a" * 64,
            "host_python_sha256": "a" * 64,
            "host_key_path": "/private/key",
        }
        probe.session = SimpleNamespace(
            identity={"window_id": "w"},
            infrastructure=lambda phase: {"calibration": {"server": {"pid": 8}}},
        )
        probe.network = SimpleNamespace(
            observe=lambda **kw: {},
            _rows=lambda kind: [],
            plan=SimpleNamespace(
                namespace="owned", host_address="10.0.0.1", client_address="10.0.0.2"
            ),
            reserve_consumer=lambda *a, **kw: None,
            register_consumer=lambda *a, **kw: None,
        )

        def bind(*args):
            now[0] = 131
            return {"identity": actual}

        probe.resources = SimpleNamespace(bind=bind)
        with monkeypatch.context() as boundary:
            boundary.setattr(mod, "file_identity", lambda p: {"sha256": "a" * 64})
            boundary.setattr(mod, "read_key", lambda p: b"k" * 32)
            boundary.setattr(mod.subprocess, "Popen", lambda *a, **kw: process)
            boundary.setattr(mod.os, "pidfd_open", lambda pid: 53, raising=False)
            boundary.setattr(mod.os, "close", lambda fd: None)
            boundary.setattr(mod, "ready_line", lambda *a, **kw: {"ready_pid": 7})
            boundary.setattr(mod, "process_snapshot", lambda pid: actual)
            boundary.setattr(
                mod.signal, "pidfd_send_signal", lambda fd, sig: sent.append(fd), raising=False
            )
            with pytest.raises(TimeoutError, match="deadline"):
                probe.run("baseline", deadline_ns=130)
        assert sent == [53]
        assert not ledger.records("calibration-result")
        assert (
            ledger.records("calibration-error")[0]["body"]["disposition"]
            == "client-terminated-failure-retained"
        )


def test_fixed_phase_retains_control_return_after_original_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.execution_capacity.attempt import AttemptLedger
    from scripts.execution_capacity.reference_lifecycle import ReferenceAttempt

    now = [50]
    monkeypatch.setattr("time.monotonic_ns", lambda: now[0])
    with AttemptLedger.create(tmp_path / "late-return", {}) as ledger:
        controller = object.__new__(ReferenceAttempt)
        controller.ledger, controller.clock_id = ledger, "clock"
        controller.vm = SimpleNamespace(window_id="w")
        controller.schedule = SimpleNamespace(phase_budget_ns=100)

        def run(phase, **kwargs):
            now[0] = 140

        controller.calibration = SimpleNamespace(run=run)
        with pytest.raises(TimeoutError, match="deadline"):
            controller._calibrate("window", scheduled_ns=30)
        row = ledger.records("calibration-phase-interval")[0]["body"]
        assert row["deadline_ns"] == 130
        assert row["after_ns"] == 140
