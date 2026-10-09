"""Fixed host guest-control lifecycle. No report/cleanup success is produced.

Every effect has a durable intent. Response loss consumes START permanently;
reconciliation observes the exact process but never repeats the operation.
The caller owns the already authenticated QGA channel and actual native facts.
"""

import json
import re
import time
from hashlib import sha256
from uuid import UUID, uuid5

from scripts.execution_capacity.attempt import digest, encode
from scripts.execution_capacity.reference_protocol import Action


def validate_control(ledger, timeout):
    ledger.bind_clock()
    if any(
        re.fullmatch(r"[0-9a-f]{64}", ledger.plan.get(key, "")) is None
        for key in ("guest_bridge_sha256", "guest_python_sha256")
    ):
        raise ValueError("preregistered guest OS helper/Python hashes required")
    rule = ledger.plan.get("guest_control")
    if not isinstance(rule, dict) or set(rule) != {
        "command_timeout_ns",
        "command_count_bound",
        "poll_count_bound",
    }:
        raise ValueError("immutable guest control bounds required")
    if (
        any(type(v) is not int or v <= 0 for v in rule.values())
        or rule["command_timeout_ns"] != timeout
        or timeout > 30_000_000_000
        or rule["command_count_bound"] > 100000
        or rule["poll_count_bound"] > 1000000
    ):
        raise ValueError("invalid or changed preregistered control bounds")


class GuestSession:
    def __init__(self, agent, ledger, identity, *, command_timeout_ns=2_000_000_000):
        validate_control(ledger, command_timeout_ns)
        for key in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id"):
            UUID(identity[key])
        intent = {k: v for k, v in identity.items() if k != "boot_id"}
        if intent not in ledger.plan.get("guest_sessions", []):
            raise ValueError("guest intent absent from immutable host plan")
        if not any(r["body"]["identity"] == identity for r in ledger.records("guest-discovered")):
            raise ValueError("actual fresh boot discovery required")
        self.agent, self.ledger = agent, ledger
        self.identity = json.loads(json.dumps(identity))
        self.timeout_ns = command_timeout_ns
        self.lock = ledger.control_lock
        starts = [
            r["body"]["pid"]
            for r in ledger.records("guest-start-pid")
            if r["body"]["identity"] == identity
        ]
        if len(starts) > 1:
            raise ValueError("multiple guest start PIDs")
        self.start_pid = starts[0] if starts else None
        with self.lock:
            self._recover_status_receipts()
            self._recover_progress_receipts()

    @classmethod
    def discover(cls, agent, ledger, intent, *, command_timeout_ns=2_000_000_000):
        with ledger.control_lock:
            return cls._discover(agent, ledger, intent, command_timeout_ns=command_timeout_ns)

    @classmethod
    def _discover(cls, agent, ledger, intent, *, command_timeout_ns):
        validate_control(ledger, command_timeout_ns)
        if intent not in ledger.plan.get("guest_sessions", []) or "boot_id" in intent:
            raise ValueError("immutable boot-independent intent required")
        if any(r["body"]["identity"] == intent for r in ledger.records("guest-discovery-intent")):
            raise ValueError("discovery already consumed; reconcile recorded operation")
        # Construct only the transport state needed before actual boot binding.
        session = object.__new__(cls)
        session.agent, session.ledger, session.identity = agent, ledger, intent
        session.timeout_ns, session.lock = command_timeout_ns, ledger.control_lock
        ledger.append(
            "guest-discovery-intent", {"identity": intent, "host_ns": time.monotonic_ns()}
        )
        row = session._command(Action.STATUS, {"phase": "discover"}, bind=False)
        actual = row.get("identity", {})
        if {k: v for k, v in actual.items() if k != "boot_id"} != intent or "boot_id" not in actual:
            raise ValueError("discovered guest identity differs")
        UUID(actual["boot_id"])
        if row.get("start_process") is not None:
            raise ValueError("fresh guest already contains this start; retained")
        if any(
            r["body"]["identity"]["boot_id"] == actual["boot_id"]
            for r in ledger.records("guest-discovered")
        ):
            raise ValueError("guest boot reused across cold sessions")
        ledger.append(
            "guest-discovered",
            {"identity": actual, "observation": row, "host_ns": time.monotonic_ns()},
        )
        return cls(agent, ledger, actual, command_timeout_ns=command_timeout_ns)

    def _poll_fence(self):
        outcomes = {
            r["body"]["poll_id"]: r["body"]
            for r in self.ledger.records("guest-status-poll-outcome")
        }
        for row in self.ledger.records("guest-status-poll-intent"):
            outcome = outcomes.get(row["body"]["poll_id"])
            if outcome is None or outcome["state"] == "unknown":
                raise ValueError("uncertain consumable status poll; retained, never repoll")

    def _pending_shorts(self):
        # Scope is the shared ledger, not this in-memory session or channel.
        settled = {r["body"]["command_id"] for r in self.ledger.records("guest-control-result")}
        settled.update(
            r["body"]["command_id"]
            for r in self.ledger.records("guest-status-poll-outcome")
            if r["body"]["owner"] == "short"
            and r["body"]["state"] in {"terminal-valid", "terminal-invalid"}
        )
        return [
            r["body"]
            for r in self.ledger.records("guest-control-intent")
            if r["body"]["command_id"] not in settled
        ]

    def _require_no_pending_short(self):
        self._poll_fence()
        self._recover_status_receipts()
        if self._pending_shorts():
            raise ValueError("unresolved short guest command; exact settlement required")

    def _validate_short(self, output, pid, command):
        row = json.loads(output)
        request, action = command["request"], command["action"]
        bind = command.get("bind_response", request.get("phase") != "discover")
        if not isinstance(row, dict) or (bind and row.get("identity") != request["identity"]):
            raise ValueError("guest response identity differs")
        process = row.get("bridge_process", {})
        argv = [
            b"/usr/bin/python3",
            b"-I",
            b"/opt/opencitadel-capacity/guest_bridge.py",
            action.encode(),
            encode(request),
        ]
        expected_argv = sha256(b"\0".join(argv) + b"\0").hexdigest()
        if (
            process.get("pid") != pid
            or process.get("argv_digest") != expected_argv
            or not process.get("cgroup")
            or type(process.get("start_ticks")) is not int
            or process["start_ticks"] <= 0
            or row.get("bridge_sha256") != self.ledger.plan["guest_bridge_sha256"]
            or process.get("executable_sha256") != self.ledger.plan["guest_python_sha256"]
        ):
            raise ValueError("actual QGA helper process/build differs")
        return row

    def _poll(self, pid, *, owner, command=None):
        """One consumable QGA status operation with durable intent/outcome.

        An unknown reply never grants another poll. A known running reply may
        be polled later. Terminal data is validated and retained in ONE outcome.
        """
        self._poll_fence()
        self._check_count("guest-status-poll-intent", "poll_count_bound")
        command_id = None if command is None else command["command_id"]
        body = {
            "identity": self.identity,
            "owner": owner,
            "command_id": command_id,
            "pid": pid,
            "host_ns": time.monotonic_ns(),
        }
        poll_id = digest({**body, "ledger_sequence": len(self.ledger.rows) + 1})
        body["poll_id"] = poll_id
        self.ledger.append("guest-status-poll-intent", body)
        try:
            output = self.agent.status(pid)
        except BaseException as error:
            self.ledger.append(
                "guest-status-poll-outcome",
                {
                    **body,
                    "state": "unknown",
                    "host_ns": time.monotonic_ns(),
                    "error": type(error).__name__,
                },
            )
            raise
        observed = time.monotonic_ns()
        outcome = {**body, "host_ns": observed, "bytes": 0 if output is None else len(output)}
        if output is None:
            outcome["state"] = "running"
        else:
            try:
                if owner == "start":
                    if output:
                        raise ValueError("uncaptured START unexpectedly returned output")
                else:
                    outcome["response"] = self._validate_short(output, pid, command)
                outcome["state"] = "terminal-valid"
            except BaseException as error:
                self.ledger.append(
                    "guest-status-poll-outcome",
                    {**outcome, "state": "terminal-invalid", "error": type(error).__name__},
                )
                raise
        self.ledger.append("guest-status-poll-outcome", outcome)
        return outcome

    def reconcile_short(self):
        """Poll only a known unresolved PID, never execute/replay another helper.

        Returns physical settlement observations; original timeout/errors remain.
        Lost exec/status replies cannot be recovered by replacing the channel.
        """
        with self.lock:
            self._poll_fence()
            pending = self._pending_shorts()
            if len(pending) != 1 or pending[0]["request"]["identity"] != self.identity:
                raise ValueError("exact unresolved short command for this boot required")
            command = pending[0]
            pids = [
                r["body"]["pid"]
                for r in self.ledger.records("guest-control-pid")
                if r["body"]["command_id"] == command["command_id"]
            ]
            if len(pids) != 1:
                raise ValueError("unresolved short exec PID unknown; retained")
            return self._poll(pids[0], owner="short", command=command)

    def _command(self, action, fields=None, *, bind=True, timeout_ns=None):
        with self.lock:
            self._require_no_pending_short()
            timeout_ns = self.timeout_ns if timeout_ns is None else timeout_ns
            request = {"identity": self.identity, **(fields or {})}
            if action in (Action.READY, Action.DONE, Action.ABORT) and any(
                r["body"]["action"] == action.value
                and r["body"]["request"]["identity"] == self.identity
                for r in self.ledger.records("guest-control-intent")
            ):
                raise ValueError("guest state transition already consumed; no retry")
            self._check_count("guest-control-intent", "command_count_bound")
            started = time.monotonic_ns()
            command_id = digest(
                {"request": request, "action": action.value, "sequence": len(self.ledger.rows) + 1}
            )
            command = {
                "command_id": command_id,
                "action": action.value,
                "request": request,
                "bind_response": bind,
                "host_ns": started,
            }
            self.ledger.append("guest-control-intent", command)
            pid = None
            try:
                pid = self.agent.execute(action, request)
                self.ledger.append(
                    "guest-control-pid",
                    {"command_id": command_id, "pid": pid, "host_ns": time.monotonic_ns()},
                )
                while time.monotonic_ns() < started + timeout_ns:
                    outcome = self._poll(pid, owner="short", command=command)
                    if outcome["state"] == "terminal-valid":
                        if outcome["host_ns"] > started + timeout_ns:
                            raise TimeoutError("guest command completed after fixed timeout")
                        self.ledger.append(
                            "guest-control-result",
                            {
                                "command_id": command_id,
                                "host_ns": outcome["host_ns"],
                                "response": outcome["response"],
                            },
                        )
                        self.last_completion_ns = outcome["host_ns"]
                        return outcome["response"]
                    time.sleep(0.01)
                raise TimeoutError("guest command timeout; no retry")
            except BaseException as error:
                self.ledger.append(
                    "guest-control-error",
                    {
                        "command_id": command_id,
                        "pid": pid,
                        "error": type(error).__name__,
                        "host_ns": time.monotonic_ns(),
                        "disposition": "retained",
                    },
                )
                raise

    def seal_phase(self, phase, *, artifact=None, offset=None):
        if phase not in {"cleanup", "stop", "offline", "read"}:
            raise ValueError("fixed seal phase required")
        from scripts.execution_capacity.evidence_bounds import parse_evidence_limits

        rule = self.ledger.plan["seal"]
        evidence_limits = parse_evidence_limits(rule.get("evidence_limits"))
        if not 1 <= rule["phase_timeout_seconds"] <= 3600:
            raise ValueError("preregistered seal phase timeout invalid")
        fields = {"phase": phase, "config_digest": rule["config_digest"]}
        if phase == "read":
            if type(offset) is not int or offset < 0:
                raise ValueError("fixed artifact page required")
            from scripts.execution_capacity.guest_seal_entry import artifact_relative

            artifact_relative(artifact)
            fields.update(artifact=artifact, offset=offset)
        with self.lock:
            if phase != "read":
                if any(
                    r["body"]["identity"] == self.identity and r["body"]["phase"] == phase
                    for r in self.ledger.records("guest-seal-intent")
                ):
                    raise ValueError("seal phase consumed; no retry")
                self.ledger.append(
                    "guest-seal-intent",
                    {"identity": self.identity, "phase": phase, "host_ns": time.monotonic_ns()},
                )
            row = self._command(
                Action.SEAL, fields, timeout_ns=rule["phase_timeout_seconds"] * 1_000_000_000
            )
            if parse_evidence_limits(row.get("evidence_limits")) != evidence_limits:
                raise ValueError("actual seal evidence limits differ from immutable Plan")
            if (
                row.get("phase") != phase
                or row.get("protocol_id") != rule["export"]["protocol_id"]
                or row.get("config_digest") != rule["config_digest"]
                or row.get("seal_helper_sha256") != rule["helper_sha256"]
                or row.get("observer_python_sha256") != rule["python_sha256"]
            ):
                raise ValueError("actual seal helper/build response differs")
            self.ledger.append(
                "guest-seal-result",
                {
                    "identity": self.identity,
                    "phase": phase,
                    "response_digest": digest(row),
                    "host_ns": time.monotonic_ns(),
                },
            )
            return row

    def infrastructure(self, phase):
        """Fixed provisioned resource/service operations. No clean/report receipt."""
        if phase not in {"resources", "start", "observe", "stop"}:
            raise ValueError("fixed infrastructure phase required")
        with self.lock:
            config = self.ledger.plan["calibration"]
            for key in ("infrastructure_sha256", "service_sha256"):
                if re.fullmatch(r"[0-9a-f]{64}", config[key]) is None:
                    raise ValueError("preregistered infrastructure build required")
            if phase in {"start", "stop"}:
                if any(
                    r["body"]["identity"] == self.identity and r["body"]["phase"] == phase
                    for r in self.ledger.records("guest-infrastructure-intent")
                ):
                    raise ValueError("infrastructure operation consumed; read back retained state")
                self._require_no_pending_short()
                self.ledger.append(
                    "guest-infrastructure-intent",
                    {"identity": self.identity, "phase": phase, "host_ns": time.monotonic_ns()},
                )
            prior = [
                r["body"]["response"]["calibration"]["server"]
                for r in self.ledger.records("guest-infrastructure-result")
                if r["body"]["identity"] == self.identity
                and r["body"]["phase"] in {"start", "observe"}
            ]
            fields = {"phase": phase}
            if phase == "stop":
                if not prior:
                    raise ValueError("observed calibration process required before stop")
                fields["server"] = prior[0]
            row = self._command(Action.INFRASTRUCTURE, fields)
            if (
                row.get("phase") != phase
                or row.get("infrastructure_sha256") != config["infrastructure_sha256"]
            ):
                raise ValueError("provisioned infrastructure response differs")
            if phase in {"start", "observe"}:
                server = row["calibration"]["server"]
                if (
                    server["boot_id"] != self.identity["boot_id"]
                    or server["service_sha256"] != config["service_sha256"]
                    or (prior and server != prior[0])
                ):
                    raise ValueError("calibration service boot/build differs")
            elif phase == "resources":
                from scripts.execution_capacity.reference_resources import cpus, validate_pg

                if row["architecture"] != "x86_64" or len(cpus(row["online_cpus"])) != 16:
                    raise ValueError("actual guest CPU architecture/allocation differs")
                validate_pg(row["postgres"])
            elif row.get("disposition") != "service-exit-observed":
                raise ValueError("calibration exit observation missing")
            self.ledger.append(
                "guest-infrastructure-result",
                {
                    "identity": self.identity,
                    "phase": phase,
                    "response": row,
                    "host_ns": time.monotonic_ns(),
                },
            )
            return row

    def _check_count(self, kind, bound):
        count = self.ledger.count(kind)
        if bound == "poll_count_bound":
            count += self.ledger.count("guest-start-poll-intent") + self.ledger.count(
                "guest-control-poll"
            )
        if bound == "command_count_bound":
            count += self.ledger.count("guest-start-intent")
        if count >= self.ledger.plan["guest_control"][bound]:
            raise ValueError("preregistered guest control count exhausted")

    def poll_start(self):
        """Return a durable terminal observation, or make one identified poll."""
        with self.lock:
            self._poll_fence()
            if self.start_pid is None:
                raise ValueError("no acknowledged QGA start PID; retained")
            if any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-start-exited")
            ):
                return True
            prior = [
                r["body"]
                for r in self.ledger.records("guest-status-poll-outcome")
                if r["body"]["owner"] == "start"
                and r["body"]["identity"] == self.identity
                and r["body"]["pid"] == self.start_pid
            ]
            terminal = next((o for o in prior if o["state"].startswith("terminal-")), None)
            if terminal is not None and terminal["state"] != "terminal-valid":
                raise ValueError("invalid consumed terminal status poll; retained")
            # Old unpaired START poll intents never establish a safe retry.
            if terminal is None and any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-start-poll-intent")
            ):
                raise ValueError("uncertain legacy status poll; retained")
            outcome = (
                terminal if terminal is not None else self._poll(self.start_pid, owner="start")
            )
            if outcome["state"] == "terminal-valid":
                self.ledger.append(
                    "guest-start-exited",
                    {
                        "identity": self.identity,
                        "pid": self.start_pid,
                        "host_ns": outcome["host_ns"],
                        "poll_id": outcome["poll_id"],
                        "exited": True,
                    },
                )
                return True
            return False

    def start(self):
        with self.lock:
            if any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-start-intent")
            ):
                raise ValueError("guest start already consumed, including response loss")
            if not any(
                r["body"]["sample_id"] == self.identity["sample_id"]
                and r["body"]["window_id"] == self.identity["window_id"]
                for r in self.ledger.records("reserved")
            ):
                raise ValueError("host reservation required before guest start")
            self._require_no_pending_short()
            self._check_count("guest-control-intent", "command_count_bound")
            self.ledger.append(
                "guest-start-intent", {"identity": self.identity, "host_ns": time.monotonic_ns()}
            )
            try:
                self.start_pid = self.agent.execute(Action.START, {"identity": self.identity})
                self.ledger.append(
                    "guest-start-pid",
                    {
                        "identity": self.identity,
                        "pid": self.start_pid,
                        "host_ns": time.monotonic_ns(),
                    },
                )
                return self.start_pid
            except BaseException as error:
                self.ledger.append(
                    "guest-control-error",
                    {
                        "identity": self.identity,
                        "action": Action.START.value,
                        "error": type(error).__name__,
                        "disposition": "uncertain-start-retained",
                    },
                )
                raise

    def reconcile(self):
        """An observed process never recovers a lost QGA response or proves success."""
        return self._command(Action.STATUS, {"phase": "reconcile"})

    def _recover_status_receipts(self):
        """Select the first durable validated receipt, never the current time.

        New terminal outcomes atomically retain validation and receive time.
        Legacy validated results remain authority for already-written journals.
        Derived rows are recoverable indexes, not a second choice of anchor.
        """
        commands = {
            r["body"]["command_id"]: r["body"] for r in self.ledger.records("guest-control-intent")
        }
        candidates = list(self.ledger.records("guest-control-result"))
        candidates.extend(
            r
            for r in self.ledger.records("guest-status-poll-outcome")
            if r["body"]["owner"] == "short" and r["body"]["state"] == "terminal-valid"
        )
        first = {}
        for saved in sorted(candidates, key=lambda r: r["sequence"]):
            receipt = saved["body"]
            command = commands.get(receipt["command_id"])
            if (
                command is None
                or command["action"] != Action.STATUS.value
                or command["request"] != {"identity": self.identity}
            ):
                continue
            row = receipt["response"]
            for field, kind in (
                ("minimal_ready", "guest-metadata-anchor"),
                ("cohort", "guest-cohort-received"),
                ("running", "guest-window-open-received"),
                ("measurement_closed", "guest-measurement-closed-received"),
            ):
                if kind in first or row.get(field) is None:
                    continue
                if row.get("process", {}).get("same_process") is not True:
                    raise ValueError("earliest guest receipt lacks live helper identity; retained")
                body = {"identity": self.identity, "host_ns": receipt["host_ns"]}
                if field == "minimal_ready":
                    ready = row[field].get("guest_ns")
                    if type(ready) is not int or ready < 0:
                        raise ValueError("earliest metadata-ready receipt is invalid; retained")
                    body["guest_ns"] = ready
                else:
                    body["observation"] = row[field]
                first[kind] = body
        for kind, body in first.items():
            existing = [
                r["body"]
                for r in self.ledger.records(kind)
                if r["body"]["identity"] == self.identity
            ]
            if existing and existing != [body]:
                raise ValueError("derived guest receipt differs from earliest durable response")
            if not existing:
                self.ledger.append(kind, body)

    def status(self):
        with self.lock:
            self._recover_status_receipts()
            row = self._command(Action.STATUS)
            self._recover_status_receipts()
            return row

    def run_markers(self):
        """Blocking fixed schedule; C3 may run this on its dedicated control thread.

        Commands share one lock. Waiting behind a status/readiness call counts as
        delay, never shifts the schedule. Remaining slots are retained on failure.
        """
        window = self.identity["window_id"]
        rule = self.ledger.plan.get("marker_schedules", {}).get(window)
        if (
            not isinstance(rule, dict)
            or set(rule) != {"anchor", "cadence_ns", "count", "timeout_ns"}
            or rule["anchor"] != "first-minimal-ready-receipt"
        ):
            raise ValueError("fixed preregistered marker schedule required")
        if (
            any(
                type(rule[k]) is not int or rule[k] <= 0
                for k in ("cadence_ns", "count", "timeout_ns")
            )
            or rule["count"] > 10000
            or rule["timeout_ns"] > 30_000_000_000
        ):
            raise ValueError("marker schedule bounds invalid")
        with self.lock:
            self._recover_status_receipts()
            anchors = [
                r["body"]
                for r in self.ledger.records("guest-metadata-anchor")
                if r["body"]["identity"] == self.identity
            ]
            if len(anchors) != 1:
                raise ValueError("exact first metadata receipt anchor required")
            if any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-markers-intent")
            ):
                raise ValueError("marker schedule already consumed")
            anchor = anchors[0]["host_ns"]
            self.ledger.append(
                "guest-markers-intent",
                {"identity": self.identity, "anchor_ns": anchor, "rule": rule},
            )
        failure = None
        observations = []
        for index in range(rule["count"]):
            sequence = index + 1
            scheduled = anchor + index * rule["cadence_ns"]
            marker_id = str(uuid5(UUID(self.identity["nonce"]), str(sequence)))
            slot = {
                "identity": self.identity,
                "marker_id": marker_id,
                "sequence": sequence,
                "scheduled_ns": scheduled,
            }
            if failure is not None:
                with self.lock:
                    self.ledger.append(
                        "guest-marker-slot", {**slot, "disposition": "not-dispatched-after-error"}
                    )
                continue
            remaining = scheduled - time.monotonic_ns()
            if remaining > 0:
                time.sleep(remaining / 1e9)
            with self.lock:
                try:
                    sent = time.monotonic_ns()
                    if sent > scheduled + rule["timeout_ns"]:
                        raise TimeoutError("marker missed fixed slot timeout")
                    # Durable before dispatch, while the control lock is held.
                    self.ledger.append("guest-marker-send", {**slot, "sent_ns": sent})
                    row = self._command(
                        Action.STAMP,
                        {"marker_id": marker_id, "sequence": sequence},
                        timeout_ns=rule["timeout_ns"],
                    )
                    if (
                        row.get("marker_id") != marker_id
                        or row.get("sequence") != sequence
                        or type(row.get("installed_ns")) is not int
                    ):
                        raise ValueError("guest marker acknowledgement differs")
                    if self.last_completion_ns > scheduled + rule["timeout_ns"]:
                        raise TimeoutError("marker completion missed fixed slot timeout")
                    observed = {
                        **slot,
                        "sent_ns": sent,
                        "completed_ns": self.last_completion_ns,
                        "guest_installed_ns": row["installed_ns"],
                        "disposition": "acknowledged",
                    }
                    self.ledger.append("guest-marker-slot", observed)
                    observations.append(observed)
                except BaseException as error:  # noqa: BLE001 - re-raised after retaining every slot
                    failure = error
                    self.ledger.append(
                        "guest-marker-slot",
                        {
                            **slot,
                            "disposition": "error",
                            "error": type(error).__name__,
                            "observed_ns": time.monotonic_ns(),
                        },
                    )
        if failure is not None:
            raise failure
        return observations

    def ready(self, *, cohort_digest, native_digest):
        with self.lock:
            self._recover_status_receipts()
            observed = [
                r["body"]["observation"]
                for r in self.ledger.records("guest-cohort-received")
                if r["body"]["identity"] == self.identity
            ]
            if len(observed) != 1 or observed[0].get("cohort_digest") != cohort_digest:
                raise ValueError("readiness requires actual received cohort")
            return self._command(
                Action.READY, {"cohort_digest": cohort_digest, "native_digest": native_digest}
            )

    def done(self, *, native_digest):
        with self.lock:
            self._recover_status_receipts()
            if not any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-window-open-received")
            ):
                raise ValueError("client-done requires host receipt of actual open window")
            return self._command(Action.DONE, {"native_digest": native_digest})

    def abort(self):
        return self._command(Action.ABORT)

    def result(self):
        with self.lock:
            if not any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-start-exited")
            ):
                raise ValueError("result requires observed exact QGA start exit")
            return self._command(Action.RESULT)

    def source_shards(self, kind):
        """Bounded exact source export; does not produce a complete workload role."""
        from scripts.acceptance.capacity_models import SourceShard
        from scripts.execution_capacity.source_export import MAX_ROWS, PAGE_ROWS

        if kind not in {"metadata", "snapshots", "ticks", "progress"}:
            raise ValueError("fixed source kind required")
        with self.lock:
            if not any(
                r["body"]["identity"] == self.identity
                for r in self.ledger.records("guest-start-exited")
            ):
                raise ValueError("source export requires exact START exit")
            pages, total, count = [], None, None
            for index in range((MAX_ROWS + PAGE_ROWS - 1) // PAGE_ROWS):
                row = self._command(Action.RESULT, {"kind": kind, "index": index})
                page = SourceShard.model_validate(row["source_shard"])
                if (
                    page.window_id != self.identity["window_id"]
                    or page.boot_id != self.identity["boot_id"]
                    or page.kind != kind
                    or page.index != index
                    or page.digest != digest([r.model_dump() for r in page.rows])
                    or page.count != (page.total + PAGE_ROWS - 1) // PAGE_ROWS
                    or page.total > MAX_ROWS
                ):
                    raise ValueError("source shard identity/count/digest mismatch")
                if index == 0:
                    total, count = page.total, page.count
                if (page.total, page.count) != (total, count):
                    raise ValueError("source export changed across pages")
                pages.append(page)
                if index + 1 == count:
                    if sum(len(p.rows) for p in pages) != total:
                        raise ValueError("source shard coverage missing")
                    return pages
            raise ValueError("source export exceeds bound")

    def _recover_progress_receipts(self):
        """Derive feed receipts only from earliest durably validated QGA outcome."""
        from scripts.acceptance.capacity_models import ProgressPage
        from scripts.execution_capacity.source_export import MAX_ROWS, PAGE_ROWS

        commands = {
            r["body"]["command_id"]: r["body"] for r in self.ledger.records("guest-control-intent")
        }
        saved = list(self.ledger.records("guest-control-result")) + [
            r
            for r in self.ledger.records("guest-status-poll-outcome")
            if r["body"]["owner"] == "short" and r["body"]["state"] == "terminal-valid"
        ]
        seen, cursor = set(), 0
        for row in sorted(saved, key=lambda r: r["sequence"]):
            receipt = row["body"]
            command = commands.get(receipt["command_id"])
            if (
                command is None
                or command["action"] != Action.PROGRESS.value
                or command["request"]["identity"] != self.identity
                or receipt["command_id"] in seen
            ):
                continue
            seen.add(receipt["command_id"])
            page = ProgressPage.model_validate(receipt["response"]["progress_page"])
            if (
                page.window_id != self.identity["window_id"]
                or page.boot_id != self.identity["boot_id"]
                or page.cursor != command["request"]["cursor"]
                or page.cursor != cursor
                or page.next_cursor != cursor + len(page.rows)
                or not page.next_cursor <= page.total <= MAX_ROWS
                or len(page.rows) != min(PAGE_ROWS, page.total - cursor)
                or page.digest != digest([r.model_dump() for r in page.rows])
            ):
                raise ValueError("incremental progress page identity/cursor/digest differs")
            for joined in page.rows:
                p = joined.progress
                if (
                    p.window_id != page.window_id
                    or p.boot_id != page.boot_id
                    or not p.after_ns <= joined.query_before_ns <= joined.query_after_ns
                ):
                    raise ValueError("incremental progress SQL/clock binding differs")
            body = {
                "identity": self.identity,
                "command_id": receipt["command_id"],
                "host_ns": receipt["host_ns"],
                "page": page.model_dump(),
            }
            prior = [
                r["body"]
                for r in self.ledger.records("guest-progress-page")
                if r["body"]["command_id"] == receipt["command_id"]
            ]
            if prior and prior != [body]:
                raise ValueError("incremental receipt differs from earliest durable outcome")
            if not prior:
                self.ledger.append("guest-progress-page", body)
            cursor = page.next_cursor

    def progress_cursor(self):
        pages = [
            r["body"]["page"]
            for r in self.ledger.records("guest-progress-page")
            if r["body"]["identity"] == self.identity
        ]
        return pages[-1]["next_cursor"] if pages else 0

    def progress(self):
        with self.lock:
            self._recover_progress_receipts()
            row = self._command(Action.PROGRESS, {"cursor": self.progress_cursor()})
            self._recover_progress_receipts()
            return row["progress_page"]
