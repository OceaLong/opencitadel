"""Offline host control tests: guest transport and clocks are substituted."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from scripts.execution_capacity.attempt import AttemptLedger, encode


@pytest.fixture(autouse=True)
def observed_host_clock(monkeypatch):
    monkeypatch.setattr(
        "scripts.execution_capacity.attempt.host_clock",
        lambda: {
            "boot_id": "same-test-boot",
            "clock": "CLOCK_MONOTONIC",
            "namespace_device": 1,
            "namespace_inode": 2,
        },
    )


def bridge_fields(pid, action, request):
    argv = [
        b"/usr/bin/python3",
        b"-I",
        b"/opt/opencitadel-capacity/guest_bridge.py",
        action.encode(),
        encode(request),
    ]
    return {
        "bridge_sha256": "b" * 64,
        "bridge_process": {
            "pid": pid,
            "start_ticks": 42,
            "executable_sha256": "c" * 64,
            "argv_digest": sha256(b"\0".join(argv) + b"\0").hexdigest(),
            "cgroup": "0::/qga",
        },
    }


def test_lost_start_is_durable_and_cannot_be_retried(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 100,
            "poll_count_bound": 1000,
        },
        "samples": [
            {
                "sample_id": identity["sample_id"],
                "window_id": identity["window_id"],
                "physical_window_id": identity["window_id"],
            }
        ],
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
    }

    class Agent:
        def execute(self, action, request):
            raise EOFError("response lost")

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.reserve(identity["sample_id"], identity["window_id"], seal_digest="b" * 64)
        session = GuestSession(Agent(), ledger, identity)
        with pytest.raises(EOFError):
            session.start()
        assert ledger.rows[-1]["kind"] == "guest-control-error"
        with pytest.raises(ValueError, match="consumed"):
            session.start()
    with (
        AttemptLedger.open(tmp_path / "ledger", plan) as ledger,
        pytest.raises(ValueError, match="consumed"),
    ):
        GuestSession(Agent(), ledger, identity).start()


def test_command_requires_exact_response_binding_and_polls_actual_pid(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 100,
            "poll_count_bound": 1000,
        },
        "samples": [],
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
    }

    class Agent:
        def execute(self, action, request):
            return 51

        def status(self, pid):
            assert pid == 51
            return json.dumps({"identity": {**identity, "boot_id": str(uuid4())}}).encode()

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(Agent(), ledger, identity)
        with pytest.raises(ValueError, match="response identity"):
            session.status()
        assert ledger.rows[-1]["kind"] == "guest-control-error"


def test_markers_use_first_metadata_receipt_and_keep_all_missed_slots(tmp_path, monkeypatch):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    rule = {
        "anchor": "first-minimal-ready-receipt",
        "cadence_ns": 100,
        "count": 3,
        "timeout_ns": 50,
    }
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 100,
            "poll_count_bound": 1000,
        },
        "samples": [],
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "marker_schedules": {identity["window_id"]: rule},
    }
    clock = [1000]
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.monotonic_ns", lambda: clock[0]
    )
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + round(seconds * 1e9)),
    )

    class Agent:
        def execute(self, action, request):
            self.request = request
            assert action.value == "stamp"
            clock[0] += 200  # Late first command; subsequent slots cannot be silently skipped.
            return 77

        def status(self, pid):
            return json.dumps(
                {
                    "identity": identity,
                    "marker_id": self.request["marker_id"],
                    "sequence": self.request["sequence"],
                    "installed_ns": 9,
                }
            ).encode()

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append("guest-metadata-anchor", {"identity": identity, "host_ns": 1000})
        session = GuestSession(Agent(), ledger, identity)
        with pytest.raises(TimeoutError):
            session.run_markers()
        slots = [r["body"] for r in ledger.rows if r["kind"] == "guest-marker-slot"]
        assert [s["scheduled_ns"] for s in slots] == [1000, 1100, 1200]
        assert [s["disposition"] for s in slots] == [
            "error",
            "not-dispatched-after-error",
            "not-dispatched-after-error",
        ]
        with pytest.raises(ValueError, match="consumed"):
            session.run_markers()


def test_actual_discovery_status_ready_done_exit_and_result_are_separate(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    intent = {k: v for k, v in identity.items() if k != "boot_id"}
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 30,
            "poll_count_bound": 100,
        },
        "guest_sessions": [intent],
        "samples": [
            {
                "sample_id": identity["sample_id"],
                "window_id": identity["window_id"],
                "physical_window_id": identity["window_id"],
            }
        ],
    }

    class Agent:
        next_pid = 40

        def __init__(self):
            self.commands = {}

        opened = False
        exited = False

        def execute(self, action, request):
            self.next_pid += 1
            self.commands[self.next_pid] = (action.value, request)
            return self.next_pid

        def status(self, pid):
            action, request = self.commands[pid]
            if action == "cold-window":
                return b"" if self.exited else None
            row = {
                "identity": identity,
                **bridge_fields(pid, action, request),
            }
            if request.get("phase") == "discover":
                row.update(
                    start_process=None,
                    phase="discover",
                    containers=[{"id": "actual"}],
                    disposition="observation-only-retained",
                )
            elif action == "status":
                row.update(
                    minimal_ready={"guest_ns": 100},
                    process={"same_process": True},
                    cohort={"cohort_digest": "b" * 64},
                    running={"start_ns": 200, "end_ns": 400} if self.opened else None,
                )
            elif action == "result":
                row["settlement"] = {"host_physical_cleanup": "pending_C"}
            return json.dumps(row).encode()

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession.discover(agent, ledger, intent)
        ledger.reserve(identity["sample_id"], identity["window_id"], seal_digest="c" * 64)
        session.start()
        assert session.poll_start() is False
        with pytest.raises(ValueError, match="received cohort"):
            session.ready(cohort_digest="b" * 64, native_digest="c" * 64)
        session.status()
        anchor = next(
            r["body"]["host_ns"] for r in ledger.rows if r["kind"] == "guest-metadata-anchor"
        )
        session.ready(cohort_digest="b" * 64, native_digest="c" * 64)
        with pytest.raises(ValueError, match="open window"):
            session.done(native_digest="d" * 64)
        agent.opened = True
        session.status()
        assert [
            r["body"]["host_ns"] for r in ledger.rows if r["kind"] == "guest-metadata-anchor"
        ] == [anchor]
        session.done(native_digest="d" * 64)
        with pytest.raises(ValueError, match="QGA start exit"):
            session.result()
        agent.exited = True
        assert session.poll_start() is True
        assert session.result()["settlement"]["host_physical_cleanup"] == "pending_C"
        assert session.poll_start() is True  # Cached durable exit, no QGA repoll.
        with pytest.raises(ValueError, match="already consumed"):
            GuestSession.discover(agent, ledger, intent)


def test_marker_success_is_joined_to_durable_send_and_single_control_lock(tmp_path, monkeypatch):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 10,
            "poll_count_bound": 10,
        },
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "samples": [],
        "marker_schedules": {
            identity["window_id"]: {
                "anchor": "first-minimal-ready-receipt",
                "cadence_ns": 100,
                "count": 3,
                "timeout_ns": 50,
            }
        },
    }
    clock = [1000]
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.monotonic_ns", lambda: clock[0]
    )
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + round(seconds * 1e9)),
    )
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append("guest-metadata-anchor", {"identity": identity, "host_ns": 1000})

        class Agent:
            def execute(self, action, request):
                self.request = request
                send = next(
                    r["body"] for r in reversed(ledger.rows) if r["kind"] == "guest-marker-send"
                )
                assert send["marker_id"] == request["marker_id"]
                assert send["sent_ns"] <= clock[0]
                return 12

            def status(self, pid):
                clock[0] += 10
                return json.dumps(
                    {
                        "identity": identity,
                        **bridge_fields(pid, "stamp", self.request),
                        "marker_id": self.request["marker_id"],
                        "sequence": self.request["sequence"],
                        "installed_ns": self.request["sequence"],
                    }
                ).encode()

        agent = Agent()
        session = GuestSession(agent, ledger, identity)
        second = GuestSession(agent, ledger, identity)
        assert second.lock is session.lock
        result = session.run_markers()
        assert [r["scheduled_ns"] for r in result] == [1000, 1100, 1200]
        assert all(r["completed_ns"] - r["sent_ns"] == 10 for r in result)
        assert len({r["marker_id"] for r in result}) == 3


@pytest.mark.parametrize("corruption", ["pid", "argv_digest", "executable_sha256", "bridge_sha256"])
def test_actual_short_helper_identity_mismatch_is_retained(tmp_path, corruption):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 1,
            "poll_count_bound": 1,
        },
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "samples": [],
    }

    class Agent:
        calls = 0

        def execute(self, action, request):
            self.calls += 1
            self.fields = bridge_fields(51, action.value, request)
            return 51

        def status(self, pid):
            if corruption == "bridge_sha256":
                self.fields[corruption] = "d" * 64
            else:
                self.fields["bridge_process"][corruption] = 52 if corruption == "pid" else "d" * 64
            return json.dumps({"identity": identity, **self.fields}).encode()

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        agent = Agent()
        session = GuestSession(agent, ledger, identity)
        with pytest.raises(ValueError, match="helper process/build"):
            session.status()
        assert ledger.rows[-1]["kind"] == "guest-control-error"
        with pytest.raises(ValueError, match="count exhausted"):
            session.status()
        assert agent.calls == 1
        with pytest.raises(ValueError, match="changed preregistered"):
            GuestSession(agent, ledger, identity, command_timeout_ns=3_000_000_000)


def test_infrastructure_start_response_loss_is_single_use(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    plan = {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 100,
            "poll_count_bound": 1000,
        },
        "samples": [],
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "calibration": {"infrastructure_sha256": "d" * 64, "service_sha256": "e" * 64},
    }

    class Agent:
        def execute(self, action, request):
            assert action.value == "infrastructure"
            assert request == {"identity": identity, "phase": "start"}
            raise EOFError("lost")

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(Agent(), ledger, identity)
        with pytest.raises(EOFError):
            session.infrastructure("start")
        with pytest.raises(ValueError, match="consumed"):
            session.infrastructure("start")


def recovery_plan():
    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    return identity, {
        "guest_bridge_sha256": "b" * 64,
        "guest_python_sha256": "c" * 64,
        "guest_control": {
            "command_timeout_ns": 2_000_000_000,
            "command_count_bound": 50,
            "poll_count_bound": 50,
        },
        "guest_sessions": [{k: v for k, v in identity.items() if k != "boot_id"}],
        "samples": [
            {
                "sample_id": identity["sample_id"],
                "window_id": identity["window_id"],
                "physical_window_id": identity["window_id"],
            }
        ],
        "calibration": {"infrastructure_sha256": "d" * 64, "service_sha256": "e" * 64},
    }


def test_unresolved_short_timeout_fences_reopen_and_infrastructure(tmp_path, monkeypatch):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()
    clock = [0]
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.monotonic_ns", lambda: clock[0]
    )

    class Agent:
        calls = 0

        def execute(self, action, request):
            self.calls += 1
            return 81

        def status(self, pid):
            clock[0] += 3_000_000_000

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(agent, ledger, identity)
        with pytest.raises(TimeoutError):
            session.status()
        with pytest.raises(ValueError, match="unresolved short"):
            session.infrastructure("observe")
        assert agent.calls == 1
    replacement = Agent()
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession(replacement, ledger, identity)
        with pytest.raises(ValueError, match="unresolved short"):
            session.status()
        assert replacement.calls == 0


@pytest.mark.parametrize("interrupt", ["lost-response", "after-terminal-outcome"])
def test_consumable_long_poll_recovery_never_polls_twice(tmp_path, monkeypatch, interrupt):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()

    class Agent:
        polls = 0

        def status(self, pid):
            self.polls += 1
            if interrupt == "lost-response":
                raise EOFError("consumed response lost")
            return b""

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append("guest-start-pid", {"identity": identity, "pid": 81})
        session = GuestSession(agent, ledger, identity)
        original = ledger.append

        def interrupted(kind, body):
            if kind == "guest-start-exited":
                raise OSError("interrupted after durable terminal outcome")
            return original(kind, body)

        monkeypatch.setattr(ledger, "append", interrupted)
        with pytest.raises((EOFError, OSError)):
            session.poll_start()
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession(agent, ledger, identity)
        if interrupt == "lost-response":
            with pytest.raises(ValueError, match=r"uncertain.*poll"):
                session.poll_start()
        else:
            assert session.poll_start() is True
        assert agent.polls == 1


@pytest.mark.parametrize(
    "interrupt_kind",
    [
        "guest-control-result",
        "guest-metadata-anchor",
        "guest-cohort-received",
        "guest-window-open-received",
    ],
)
def test_earliest_durable_status_receipts_survive_interruption(
    tmp_path, monkeypatch, interrupt_kind
):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()
    plan["marker_schedules"] = {
        identity["window_id"]: {
            "anchor": "first-minimal-ready-receipt",
            "cadence_ns": 100,
            "count": 2,
            "timeout_ns": 50,
        }
    }
    clock = [1000]
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.monotonic_ns", lambda: clock[0]
    )

    class Agent:
        calls = 0

        def execute(self, action, request):
            self.calls += 1
            self.request = request
            return 81

        def status(self, pid):
            return json.dumps(
                {
                    "identity": identity,
                    **bridge_fields(pid, "status", self.request),
                    "minimal_ready": {"guest_ns": 20},
                    "process": {"same_process": True},
                    "cohort": {"cohort_digest": "e" * 64},
                    "running": {"start_ns": 30, "end_ns": 40},
                }
            ).encode()

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(agent, ledger, identity)
        original = ledger.append

        def interrupted(kind, body):
            if kind == interrupt_kind:
                raise OSError("interrupted after durable STATUS")
            return original(kind, body)

        monkeypatch.setattr(ledger, "append", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            session.status()
    clock[0] = 10000
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession(agent, ledger, identity)
        session.status()
        for kind in (
            "guest-metadata-anchor",
            "guest-cohort-received",
            "guest-window-open-received",
        ):
            assert ledger.records(kind)[0]["body"]["host_ns"] == 1000
        calls = agent.calls
        with pytest.raises(TimeoutError, match="missed fixed slot"):
            session.run_markers()
        assert agent.calls == calls
        assert [r["body"]["scheduled_ns"] for r in ledger.records("guest-marker-slot")] == [
            1000,
            1100,
        ]


def test_known_short_settlement_releases_fence_without_replaying_effect(tmp_path, monkeypatch):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()
    clock = [0]
    monkeypatch.setattr(
        "scripts.execution_capacity.reference_session.time.monotonic_ns", lambda: clock[0]
    )

    class Agent:
        calls = 0
        running = True

        def execute(self, action, request):
            self.calls += 1
            self.action, self.request = action.value, request
            return 81

        def status(self, pid):
            if self.running:
                clock[0] += 3_000_000_000
                return None
            return json.dumps(
                {"identity": identity, **bridge_fields(pid, self.action, self.request)}
            ).encode()

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        session = GuestSession(agent, ledger, identity)
        with pytest.raises(TimeoutError):
            session.abort()
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession(agent, ledger, identity)
        assert session.reconcile_short()["state"] == "running"
        with pytest.raises(ValueError, match="unresolved short"):
            session.status()
        agent.running = False
        assert session.reconcile_short()["state"] == "terminal-valid"
        assert agent.calls == 1
        assert len(ledger.records("guest-control-error")) == 1
        with pytest.raises(ValueError, match="consumed"):
            session.abort()
        session.status()
        assert agent.calls == 2


@pytest.mark.parametrize("owner", ["start", "short"])
def test_missing_poll_outcome_is_fenced_and_consumes_bound(tmp_path, monkeypatch, owner):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()

    class Agent:
        calls = 0
        polls = 0

        def execute(self, action, request):
            self.calls += 1
            self.action, self.request = action.value, request
            return 81

        def status(self, pid):
            self.polls += 1
            return (
                b""
                if owner == "start"
                else json.dumps(
                    {"identity": identity, **bridge_fields(pid, self.action, self.request)}
                ).encode()
            )

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append("guest-start-pid", {"identity": identity, "pid": 81})
        session = GuestSession(agent, ledger, identity)
        original = ledger.append

        def interrupted(kind, body):
            if kind == "guest-status-poll-outcome":
                raise OSError("interrupted before outcome durability")
            return original(kind, body)

        monkeypatch.setattr(ledger, "append", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            session.poll_start() if owner == "start" else session.status()
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        session = GuestSession(agent, ledger, identity)
        with pytest.raises(ValueError, match=r"uncertain.*poll"):
            session.poll_start() if owner == "start" else session.reconcile_short()
        with pytest.raises(ValueError, match=r"uncertain.*poll"):
            session.infrastructure("observe")
        assert agent.polls == 1
        assert ledger.count("guest-status-poll-intent") == 1
        assert ledger.count("guest-status-poll-outcome") == 0


def test_tracked_calibration_service_does_not_keep_short_command_fence(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()
    server = {
        "boot_id": identity["boot_id"],
        "service_sha256": "e" * 64,
        "pid": 93,
        "start_ticks": 400,
    }

    class Agent:
        def __init__(self):
            self.calls = []

        def execute(self, action, request):
            self.request = request
            self.calls.append(request["phase"])
            return 81

        def status(self, pid):
            phase = self.request["phase"]
            row = {
                "identity": identity,
                "phase": phase,
                "infrastructure_sha256": "d" * 64,
                **bridge_fields(pid, "infrastructure", self.request),
            }
            if phase == "stop":
                assert self.request["server"] == server
                row["disposition"] = "service-exit-observed"
            else:
                row["calibration"] = {"server": server}
            return json.dumps(row).encode()

    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        agent = Agent()
        session = GuestSession(agent, ledger, identity)
        session.infrastructure("start")
        session.infrastructure("observe")
        session.infrastructure("stop")
        assert agent.calls == ["start", "observe", "stop"]
        assert len(ledger.records("guest-infrastructure-result")) == 3


def test_legacy_validated_status_result_recovers_original_receipt_without_io(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append(
            "guest-control-intent",
            {
                "command_id": "old",
                "action": "status",
                "request": {"identity": identity},
                "host_ns": 900,
            },
        )
        ledger.append(
            "guest-control-result",
            {
                "command_id": "old",
                "host_ns": 1000,
                "response": {
                    "identity": identity,
                    "process": {"same_process": True},
                    "minimal_ready": {"guest_ns": 20},
                    "cohort": {"cohort_digest": "e" * 64},
                    "running": {"start_ns": 30, "end_ns": 40},
                },
            },
        )
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        GuestSession(object(), ledger, identity)  # No transport methods exist.
        for kind in (
            "guest-metadata-anchor",
            "guest-cohort-received",
            "guest-window-open-received",
        ):
            assert ledger.records(kind)[0]["body"]["host_ns"] == 1000


def test_invalid_consumed_start_reply_is_terminal_failure_not_repolled(tmp_path):
    from scripts.execution_capacity.reference_session import GuestSession

    identity, plan = recovery_plan()

    class Agent:
        calls = 0

        def status(self, pid):
            self.calls += 1
            return b"unexpected stdout"

    agent = Agent()
    with AttemptLedger.create(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        ledger.append("guest-discovered", {"identity": identity})
        ledger.append("guest-start-pid", {"identity": identity, "pid": 81})
        with pytest.raises(ValueError, match="unexpectedly returned output"):
            GuestSession(agent, ledger, identity).poll_start()
    with AttemptLedger.open(tmp_path / "ledger", plan) as ledger:
        ledger.bind_clock()
        with pytest.raises(ValueError, match="invalid consumed terminal"):
            GuestSession(agent, ledger, identity).poll_start()
        assert agent.calls == 1
        assert ledger.count("guest-status-poll-intent") == 1
