"""Single-use host lifecycle. Explicit calls have effects; import has none.

Native observations are supplied by C3 through the fixed typed boundary. This
module never certifies C2c cleanup, seals a forced exit or deletes an overlay.
"""

import contextlib
import os
import time
from pathlib import Path
from uuid import UUID

from scripts.acceptance.capacity_models import ID, Digest, Plan, Record, WindowPlan
from scripts.execution_capacity.attempt import digest
from scripts.execution_capacity.guest_bridge import process_snapshot
from scripts.execution_capacity.reference_calibration import Calibration
from scripts.execution_capacity.reference_protocol import GuestAgent
from scripts.execution_capacity.reference_round import verify_round
from scripts.execution_capacity.reference_schedule import CalibrationSchedule, validate_phases
from scripts.execution_capacity.reference_session import GuestSession
from scripts.execution_capacity.reference_vm import vm_plan_record


class StageLedger:
    def __init__(self, ledger, window_id):
        self.ledger, self.window_id = ledger, window_id

    def rows(self, kind):
        return [
            r["body"]
            for r in self.ledger.records(kind)
            if r["body"].get("window_id") == self.window_id
        ]

    def require_active(self):
        if self.rows("lifecycle-failure") or self.rows("lifecycle-recovery-intent"):
            raise ValueError("failed lifecycle retained; continuation forbidden")

    def require(self, predecessors):
        self.require_active()
        completed = {r["stage"] for r in self.rows("lifecycle-completed")}
        if not set(predecessors) <= completed:
            raise ValueError("lifecycle predecessor observation missing")

    def run(self, stage, predecessors, effect):
        with self.ledger.control_lock:
            self.require(predecessors)
            if any(r["stage"] == stage for r in self.rows("lifecycle-intent")):
                raise ValueError("lifecycle stage consumed; uncertain effects retained")
            self.ledger.append(
                "lifecycle-intent",
                {"window_id": self.window_id, "stage": stage, "host_ns": time.monotonic_ns()},
            )
        try:
            result = effect()
            self.ledger.append(
                "lifecycle-completed",
                {"window_id": self.window_id, "stage": stage, "host_ns": time.monotonic_ns()},
            )
            return result
        except BaseException as error:
            self.ledger.append(
                "lifecycle-failure",
                {
                    "window_id": self.window_id,
                    "stage": stage,
                    "host_ns": time.monotonic_ns(),
                    "error": type(error).__name__,
                    "disposition": "retained",
                },
            )
            raise


class NativeReadyObservation(Record):
    consumer_id: ID
    context_id: ID
    session_id: ID
    page_id: ID
    subscription_id: ID
    event_digest: Digest


def child_snapshot(pid):
    observed = process_snapshot(pid)
    status = dict(
        line.split(":", 1) for line in Path("/proc", str(pid), "status").read_text().splitlines()
    )
    if int(status["PPid"]) != os.getpid():
        raise ValueError("native registration requires exact coordinator child")
    return observed


class ReferenceAttempt:
    """Actual physical stages, with an explicit C3 native-observation boundary."""

    def __init__(self, vm, network, resources, intent, *, parent):
        if not (vm.ledger is network.ledger is resources.ledger):
            raise ValueError("one attempt authority required")
        self.vm, self.network, self.resources = vm, network, resources
        self.ledger, self.intent = vm.ledger, intent
        self.round_binding = verify_round(parent, self.ledger)
        if (
            self.round_binding.round_id != intent.get("attempt_id")
            or self.round_binding.window_id != vm.window_id
            or self.round_binding.sample_id != vm.sample_id
            or intent.get("window_id") != vm.window_id
            or intent.get("sample_id") != vm.sample_id
        ):
            raise ValueError("physical round/sample/window identity differs")
        if (
            network.plan.attempt_id != self.round_binding.round_id
            or vm_plan_record(vm.plan) != self.ledger.plan["vm"]
        ):
            raise ValueError("immutable round VM/network plan differs")
        selected = [p for p in parent.plan["samples"] if p["sample_id"] == vm.sample_id]
        if len(selected) != 1:
            raise ValueError("immutable parent sample operation missing")
        self.sample_plan = Plan.model_validate(selected[0])
        if self.sample_plan.physical_window_id != vm.window_id:
            raise ValueError("immutable sample operation/window differs")
        self.window = WindowPlan.model_validate(self.ledger.plan["window_plans"][vm.window_id])
        from scripts.acceptance.capacity_completion import validate_resource_plan

        validate_resource_plan(self.sample_plan, self.window)
        self.schedule = CalibrationSchedule.model_validate(self.ledger.plan["calibration_schedule"])
        self.schedule.check_window(self.window)
        validate_phases(self.ledger.plan["calibration"]["phases"])
        self.stages = StageLedger(self.ledger, vm.window_id)
        self.qmp = self.qga = self.session = self.calibration = None
        self.native = {}
        self.ready_observations = {}
        self.marker_thread = self.calibration_thread = None
        self.thread_errors = []

    def _run(self, stage, predecessors, effect):
        return self.stages.run(stage, predecessors, effect)

    def _calibrate(self, phase, *, scheduled_ns=None):
        before = time.monotonic_ns()
        scheduled = before if scheduled_ns is None else scheduled_ns
        deadline = scheduled + self.schedule.phase_budget_ns
        after = None
        try:
            if before >= deadline:
                raise TimeoutError("calibration fixed phase deadline missed before dispatch")
            result = self.calibration.run(phase, deadline_ns=deadline)
            after = time.monotonic_ns()
            if after > deadline:
                raise TimeoutError("calibration fixed phase deadline exceeded; retained")
            return result
        finally:
            self.ledger.append(
                "calibration-phase-interval",
                {
                    "window_id": self.vm.window_id,
                    "clock_id": self.clock_id,
                    "phase": phase,
                    "before_ns": before,
                    "after_ns": time.monotonic_ns() if after is None else after,
                    "scheduled_ns": scheduled,
                    "deadline_ns": deadline,
                },
            )

    def boot(self):
        # All schedule validation above precedes reservation or physical effects.
        def begin():
            self.clock_id = self.ledger.bind_clock()
            self.resources.configure()
            self.network.preflight()
            self.network.create()
            self.vm.create_overlay()
            self.network.reserve_consumer("qemu:" + self.vm.plan.uuid, client=False)
            self.vm.launch()
            self.qmp = self.vm.open_channel("qmp", timeout=2)
            self.qmp.negotiate_qmp()
            self.vm.verify_paused(self.qmp)
            self.resources.bind_vm(self.vm, self.network)
            self.vm.resume(self.qmp)
            deadline = time.monotonic_ns() + self.schedule.boot_timeout_ns
            while True:
                self.qga = self.vm.open_channel("qga", timeout=2)
                agent = GuestAgent(self.qga)
                self.ledger.append(
                    "qga-sync-intent",
                    {"window_id": self.vm.window_id, "host_ns": time.monotonic_ns()},
                )
                try:
                    capabilities = agent.synchronize(
                        int(UUID(self.intent["nonce"])) % (2**53 - 1) + 1
                    )
                    self.ledger.append(
                        "qga-synchronized",
                        {
                            "window_id": self.vm.window_id,
                            "capabilities": capabilities,
                            "host_ns": time.monotonic_ns(),
                        },
                    )
                    break
                except (TimeoutError, OSError) as error:
                    self.qga.wire.close()
                    self.ledger.append(
                        "qga-sync-error",
                        {
                            "window_id": self.vm.window_id,
                            "error": type(error).__name__,
                            "host_ns": time.monotonic_ns(),
                        },
                    )
                    if time.monotonic_ns() >= deadline:
                        raise
                    # Only idempotent QGA sync/info, never guest-exec or status.
                    time.sleep(0.05)
            self.session = GuestSession.discover(
                agent,
                self.ledger,
                self.intent,
                command_timeout_ns=self.ledger.plan["guest_control"]["command_timeout_ns"],
            )
            self.session.infrastructure("resources")
            self.session.infrastructure("start")
            self.calibration = Calibration(self.vm, self.session, self.network, self.resources)
            self._calibrate("baseline")
            self.network.shape()
            self._calibrate("pre")

        return self._run("boot", (), begin)

    def _thread(self, stage, action):
        import threading

        def work():
            try:
                self._run(stage, ("source",), action)
            except BaseException as error:  # noqa: BLE001 - retain and report every failed ownership boundary
                self.thread_errors.append(error)

        thread = threading.Thread(target=work, name="capacity-" + stage)
        thread.start()
        return thread

    def _check_threads(self):
        if self.thread_errors:
            raise BaseExceptionGroup("retained reference control failures", self.thread_errors)

    def _status(self):
        self._check_threads()
        row = self.session.status()
        if row.get("failure") is not None or row.get("shutdown_failure") is not None:
            raise ValueError("actual guest source failure retained")
        return row

    def start_source(self):
        def start():
            self.session.start()

        self._run("source", ("boot",), start)

        def cohort():
            deadline = time.monotonic_ns() + self.schedule.boot_timeout_ns
            while time.monotonic_ns() < deadline:
                row = self._status()
                if row.get("minimal_ready") is not None and self.marker_thread is None:
                    self.marker_thread = self._thread("markers", self.session.run_markers)
                if row.get("cohort") is not None:
                    self.cohort = row["cohort"]
                    return self.cohort
                time.sleep(0.01)
            raise TimeoutError("fixed source cohort observation deadline")

        return self._run("cohort", ("source",), cohort)

    def reserve_native(self, consumer_id):
        if consumer_id not in self.ledger.plan["native_consumers"]:
            raise ValueError("native consumer absent from immutable plan")
        return self._run(
            "native-reserve:" + consumer_id,
            ("boot",),
            lambda: self.network.reserve_consumer("native:" + consumer_id, client=True),
        )

    def register_native(self, consumer_id, process):
        """C3 keeps its real launcher blocked until this method returns."""

        def register():
            expected = self.ledger.plan["native_consumers"][consumer_id]
            original = child_snapshot(process.pid)
            if any(original[k] != expected[k] for k in ("argv_digest", "executable_sha256")):
                raise ValueError("native child actual executable/argv differs")
            fd = os.pidfd_open(process.pid)
            try:
                if child_snapshot(process.pid) != original:
                    raise ValueError("native child changed during pidfd acquisition")
                allocation = self.resources.bind("client", process.pid, original)
                self.network.register_consumer(
                    process.pid,
                    allocation["identity"],
                    client=True,
                    consumer_id="native:" + consumer_id,
                )
                # The coordinator retains a separate descriptor for exact failure
                # recovery; network owns its own exit-observation descriptor.
                self.native[consumer_id] = {
                    "process": process,
                    "pidfd": fd,
                    "identity": allocation["identity"],
                }
                self.ledger.append(
                    "native-registered",
                    {
                        "window_id": self.vm.window_id,
                        "consumer_id": consumer_id,
                        "identity": allocation["identity"],
                        "host_ns": time.monotonic_ns(),
                    },
                )
            except BaseException:
                os.close(fd)
                raise

        return self._run(
            "native-register:" + consumer_id, ("native-reserve:" + consumer_id,), register
        )

    def _native_alive(self):
        if not self.native:
            raise ValueError("actual registered native clients required")
        for client in self.native.values():
            if process_snapshot(client["process"].pid) != client["identity"]:
                raise ValueError("native client process identity changed")
            self.resources.observe_process("client", client["process"].pid, client["identity"])

    def observe_native_ready(self, observation):
        """Called at C3's actual subscribed/DOM readiness event receipt.

        C3 must retain the event matching event_digest. This does not attest paint;
        completion separately consumes typed actual Measurements.
        """
        observation = NativeReadyObservation.model_validate(observation)
        with self.ledger.control_lock:
            self.stages.require(("cohort",))
            if observation.consumer_id not in self.native:
                raise ValueError("native observation requires registered owned consumer")
            self._native_alive()
            expected = {"context_id": observation.context_id, "session_id": observation.session_id}
            if expected not in self.ledger.plan["native_contexts"]:
                raise ValueError("native context/session differs from preregistration")
            if observation.context_id in self.ready_observations:
                raise ValueError("native ready observation already consumed")
            row = {
                "window_id": self.vm.window_id,
                "observation": observation.model_dump(),
                "received_ns": time.monotonic_ns(),
            }
            self.ledger.append("native-ready-observed", row)
            self.ready_observations[observation.context_id] = row
            return row

    def client_ready(self):
        def ready():
            self._native_alive()
            contexts = self.ledger.plan["native_contexts"]
            if (
                len(contexts) != 10
                or len(self.ready_observations) != 10
                or {r["session_id"] for r in contexts} != set(self.window.session_ids)
                or {r["context_id"] for r in contexts} != set(self.ready_observations)
            ):
                raise ValueError("ten preregistered actual native observers required")
            self.native_ready_digest = digest(
                [self.ready_observations[r["context_id"]] for r in contexts]
            )
            return self.session.ready(
                cohort_digest=self.cohort["cohort_digest"], native_digest=self.native_ready_digest
            )

        return self._run("ready", ("cohort",), ready)

    def open_window(self):
        def wait():
            deadline = time.monotonic_ns() + self.schedule.boot_timeout_ns
            while time.monotonic_ns() < deadline:
                row = self._status()
                if row.get("running") is not None:
                    from scripts.execution_capacity.reference_evidence import observed

                    self.open_received_ns = observed(
                        self.ledger, self.session.identity, "guest-window-open-received"
                    )["host_ns"]
                    return row["running"]
                time.sleep(0.01)
            raise TimeoutError("actual guest-open receipt deadline")

        result = self._run("open", ("ready",), wait)

        def calibrate():
            remaining = self.open_received_ns + self.schedule.window_offset_ns - time.monotonic_ns()
            if remaining > 0:
                time.sleep(remaining / 1e9)
            return self._calibrate(
                "window", scheduled_ns=self.open_received_ns + self.schedule.window_offset_ns
            )

        self.calibration_thread = self._thread("calibration-window", calibrate)
        return result

    def observe_progress(self):
        self.stages.require(("source",))
        self._status()
        return self.session.progress()

    def client_done(self, observations):
        """Consume actual typed C3 facts once; a flag/digest cannot complete work."""

        def complete():
            from types import SimpleNamespace

            from scripts.acceptance.capacity_completion import validate_completion
            from scripts.acceptance.capacity_models import Measurements, Progress
            from scripts.acceptance.capacity_timing import validate_progress_receipts
            from scripts.execution_capacity.attempt import encode
            from scripts.execution_capacity.ownership import _open_private
            from scripts.execution_capacity.reference_evidence import (
                command_interval,
                incremental_receipts,
                observed,
            )

            measurements = Measurements.model_validate(observations)
            self._check_threads()
            self._native_alive()
            if (
                measurements.attempt_id != self.round_binding.parent_attempt_id
                or measurements.protocol_id != self.ledger.plan["protocol_id"]
                or measurements.errors
                or {s.sample_id for s in measurements.samples} != {self.vm.sample_id}
                or any(
                    s.status != "ok" or s.clock_id != self.clock_id for s in measurements.samples
                )
            ):
                raise ValueError("native completion actual sample/parent/clock identity differs")
            closed = observed(
                self.ledger, self.session.identity, "guest-measurement-closed-received"
            )
            if self.session.progress_cursor() < closed["observation"]["feed_cursor"]:
                raise ValueError("measured incremental acknowledgements not yet received")
            now = time.monotonic_ns()
            ready_sent, _ = command_interval(self.ledger, self.session.identity, "client-ready")
            values, acks = incremental_receipts(self.ledger, self.session.identity, self.clock_id)
            progress = {
                key: Progress.model_validate(value)
                for key, value in values.items()
                if value["phase"] != "setup"
            }
            paints = {p.progress_id: p for p in measurements.live_paints}
            if len(paints) != len(measurements.live_paints):
                raise ValueError("duplicate actual paint observation")
            contexts = self.ledger.plan["native_contexts"]
            window = SimpleNamespace(
                host_ready_sent_ns=ready_sent,
                coordinator_end_ns=now,
                coordinator_start_ns=observed(
                    self.ledger, self.session.identity, "guest-window-open-received"
                )["host_ns"],
                session_ids=[r["session_id"] for r in contexts],
                context_ids=[r["context_id"] for r in contexts],
                claims=[
                    SimpleNamespace(run_id=run, session_id=session)
                    for session, run in self.cohort["sessions"].items()
                ],
            )
            validate_progress_receipts(
                progress,
                paints,
                [acks[k] for k in progress],
                {self.vm.window_id: window},
                self.clock_id,
            )
            if any(s.end_ns > now for s in measurements.samples) or any(
                max(p.source_ack_received_ns, p.paint_received_ns) > now
                for p in measurements.live_paints
            ):
                raise ValueError("future native completion observation")
            validate_completion(
                {self.sample_plan.sample_id: self.sample_plan},
                measurements,
                self.clock_id,
                {self.vm.window_id: window},
            )
            raw = encode(measurements.model_dump())
            if len(raw) > 32 * 1024**2:
                raise ValueError("per-round native artifact exceeds bound; retained")
            path = self.ledger.root / ("native-" + self.vm.window_id + ".json")
            with os.fdopen(
                _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
            ) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self.native_done_digest = digest(measurements.model_dump())
            self.ledger.append(
                "native-complete-observed",
                {
                    "identity": self.session.identity,
                    "host_ns": now,
                    "artifact": path.name,
                    "digest": self.native_done_digest,
                    "round_binding": self.round_binding.model_dump(),
                },
            )
            # C3 clients and source continue to original guest end. This command
            # does not release consumers, stop subscribers, or declare cleanup.
            return self.session.done(native_digest=self.native_done_digest)

        return self._run("done", ("open", "calibration-window"), complete)

    def recover_failure(self):
        """Exact owned pidfd recovery; uncertainty blocks release, never clean."""
        import select
        import signal

        with self.ledger.control_lock:
            if self.stages.rows("lifecycle-recovery-intent"):
                raise ValueError("failed recovery already consumed; inspect retained authority")
            self.ledger.append(
                "lifecycle-recovery-intent",
                {"window_id": self.vm.window_id, "host_ns": time.monotonic_ns(), "clean": False},
            )
        uncertainties = []
        if self.session is not None:
            try:
                self.session.abort()
            except BaseException as error:  # noqa: BLE001 - retain and report every failed ownership boundary
                uncertainties.append("guest-abort:" + type(error).__name__)
        for key, client in self.native.items():
            try:
                poll = select.poll()
                poll.register(client["pidfd"], select.POLLIN)
                if not poll.poll(0):
                    if process_snapshot(client["process"].pid) != client["identity"]:
                        raise ValueError("native identity changed; do not signal")
                    self.ledger.append(
                        "native-stop-intent",
                        {
                            "window_id": self.vm.window_id,
                            "consumer_id": key,
                            "identity": client["identity"],
                            "host_ns": time.monotonic_ns(),
                        },
                    )
                    with contextlib.suppress(ProcessLookupError):
                        signal.pidfd_send_signal(client["pidfd"], signal.SIGTERM)
                    if not poll.poll(5000):
                        raise TimeoutError("native exact pidfd exit missing")
                self.ledger.append(
                    "native-exited-failure",
                    {
                        "window_id": self.vm.window_id,
                        "consumer_id": key,
                        "identity": client["identity"],
                        "host_ns": time.monotonic_ns(),
                    },
                )
                os.close(client["pidfd"])
            except BaseException as error:  # noqa: BLE001 - retain and report every failed ownership boundary
                uncertainties.append("native:" + key + ":" + type(error).__name__)
        try:
            self.vm.force_exit()
        except BaseException as error:  # noqa: BLE001 - retain and report every failed ownership boundary
            uncertainties.append("vm:" + type(error).__name__)
        for thread in (self.marker_thread, self.calibration_thread):
            if thread is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    uncertainties.append("control-thread:" + thread.name)
        if not any(r.startswith(("vm:", "native:", "control-thread:")) for r in uncertainties):
            try:
                self.network.cleanup()
            except BaseException as error:  # noqa: BLE001 - retain and report every failed ownership boundary
                uncertainties.append("network:" + type(error).__name__)
        if not any(r.startswith("control-thread:") for r in uncertainties):
            for channel in (self.qmp, self.qga):
                if channel is not None:
                    channel.wire.close()
        result = {
            "window_id": self.vm.window_id,
            "host_ns": time.monotonic_ns(),
            "clean": False,
            "overlay": "retained",
            "disposition": "failed-recovery",
            "uncertainties": uncertainties,
            "cumulative_cleanup": "pending_C",
        }
        self.ledger.append("lifecycle-recovery-observed", result)
        return result

    def finish_source(self):
        """Wait original load/finite source settlement; export actual partial roles.

        Returns one real Window and its full source facts. C2c cleanup and native
        semantic/report assembly remain mandatory; no successful report is emitted.
        """

        def finish():
            from types import SimpleNamespace

            from scripts.acceptance.capacity_timing import validate_causal_window
            from scripts.execution_capacity.reference_evidence import (
                incremental_receipts,
                join_window,
            )

            deadline = time.monotonic_ns() + self.schedule.source_timeout_ns
            while True:
                self._check_threads()
                self._native_alive()
                if self.session.poll_start():
                    break
                if time.monotonic_ns() >= deadline:
                    raise TimeoutError("source START exit deadline; retained")
                time.sleep(0.01)
            for thread in (self.marker_thread, self.calibration_thread):
                if thread is not None:
                    thread.join(timeout=max(0, (deadline - time.monotonic_ns()) / 1e9))
                    if thread.is_alive():
                        raise TimeoutError("source control thread deadline; retained")
            self._check_threads()
            self._calibrate("post")
            sources = {
                kind: [row for page in self.session.source_shards(kind) for row in page.rows]
                for kind in ("metadata", "snapshots", "ticks", "progress")
            }
            if len(sources["metadata"]) != 1:
                raise ValueError("exact actual guest metadata required")
            metadata = sources["metadata"][0]
            if (
                metadata.measurement != self.window.measurement
                or metadata.source_digest != self.intent["source_digest"]
                or metadata.native_ready_digest != self.native_ready_digest
                or metadata.native_done_digest != self.native_done_digest
            ):
                raise ValueError("immutable source/native binding differs")
            contexts = [
                r["context_id"]
                for session in metadata.session_ids
                for r in self.ledger.plan["native_contexts"]
                if r["session_id"] == session
            ]
            window = join_window(
                self.ledger,
                self.session.identity,
                metadata.model_dump(),
                sources["snapshots"],
                sources["ticks"],
                contexts,
                self.round_binding.safe().model_dump(),
                self.clock_id,
            )
            protocol = SimpleNamespace(
                clock_id=self.clock_id,
                startup_ns=self.window.startup_ns,
                registered_ns=self.ledger.plan["registered_ns"],
                load_ready_ns=2_000_000_000,
            )
            validate_causal_window(protocol, self.window, window)
            early, acknowledgements = incremental_receipts(
                self.ledger, self.session.identity, self.clock_id
            )
            final = {p.progress_id: p for p in sources["progress"]}
            if len(final) != len(sources["progress"]):
                raise ValueError("duplicate final progress identity")
            for key, row in early.items():
                if window.guest_start_ns <= row["after_ns"] < window.guest_end_ns and (
                    key not in final or final[key].model_dump() != row
                ):
                    raise ValueError("incremental/final source identity differs")
            if not {p.progress_id for p in final.values() if p.phase == "measured"} <= set(early):
                raise ValueError("final measured progress lacks actual realtime acknowledgement")
            from scripts.execution_capacity.reference_evidence import calibration_intervals

            result = {
                "calibration_phase_intervals": calibration_intervals(
                    self.ledger, self.vm.window_id, self.clock_id
                ),
                "window": window,
                "progress": sources["progress"],
                "source_acks": [acknowledgements[k] for k in final if k in acknowledgements],
                "round_binding": self.round_binding.safe(),
                "cleanup": "pending_C",
            }
            self.ledger.append(
                "source-export-observed",
                {
                    "window_id": self.vm.window_id,
                    "window_digest": digest(window.model_dump()),
                    "progress_count": len(final),
                    "host_ns": time.monotonic_ns(),
                    "cleanup": "pending_C",
                },
            )
            return result

        return self._run("source-export", ("done",), finish)
