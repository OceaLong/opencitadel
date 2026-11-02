"""Pure actual-record joins; these fixtures do not assert physical measurements."""

import pytest
from scripts.acceptance.capacity_models import WindowPlan


def test_window_preregistration_is_relative_and_rejects_guessed_future_epoch():
    plan = {
        "window_id": "window",
        "startup_ns": 10_000_000_000,
        "seconds": 4,
        "calibration_window": {"offset_ns": 0, "budget_ns": 1_000_000_000},
        "measurement": {
            "start_offset_ns": 0,
            "end_offset_ns": 1_000_000_000,
            "control_margin_ns": 1_000_000_000,
        },
        "session_ids": [f"s{i}" for i in range(10)],
        "marker_count": 130,
        "marker_anchor": "first-minimal-ready-receipt",
        "trigger": "guest-open-receipt",
    }
    assert WindowPlan.model_validate(plan).startup_ns == 10_000_000_000
    with pytest.raises(ValueError, match="Extra inputs"):
        WindowPlan.model_validate({**plan, "coordinator_start_ns": 100})


def test_discrete_calibration_does_not_manufacture_spanning_transfer():
    from scripts.execution_capacity.reference_calibration import export_probes

    row = {
        "identity": {"window_id": "w"},
        "phase": "pre",
        "measurements": {
            "rows": [
                {
                    "action": "upload",
                    "started_ns": 10,
                    "finished_ns": 30,
                    "bytes": 20,
                    "transfer_ns": 10,
                    "bits_per_second": 16_000_000_000,
                }
            ],
            "errors": [],
            "attempted": 1,
            "expected": 18,
        },
    }
    with pytest.raises(ValueError, match="incomplete"):
        export_probes(row, clock_id="clock")


def test_claim_snapshot_uses_sql_predicates_without_cross_epoch_math():
    from types import SimpleNamespace

    from scripts.acceptance.capacity_models import Claim, ClaimSnapshot
    from scripts.acceptance.capacity_timing import validate_snapshots

    claim = Claim(
        run_id="r",
        activity_id="a",
        generation=1,
        claim_generation=1,
        call_identity="c",
        session_id="s",
        policy_id="p",
        configured_model="acceptance-live",
        stream=True,
        boot_id="b",
    )
    fact = {
        "run_id": "r",
        "activity_id": "a",
        "generation": 1,
        "claim_generation": 1,
        "call_identity": "c",
        "reservation_id": "c",
        "policy_id": "p",
        "claimed_by": "worker",
        "sql_observed_at": "2026-09-18T00:00:10Z",
        "call_started_at": "2026-09-18T00:00:01Z",
        "heartbeat_at": "2026-09-18T00:00:09Z",
        "claim_deadline": "2026-09-18T00:00:20Z",
        "timeout_at": "2026-09-18T00:01:00Z",
        "lease_live": True,
        "settled": False,
        "terminal": False,
        "reservation_state": "dispatching",
        "status": "call_started",
        "configured_model": "acceptance-live",
        "stream": True,
    }
    window = SimpleNamespace(
        boot_id="b", guest_start_ns=2_000_000_000, guest_end_ns=2_100_000_000, claims=[claim]
    )
    snapshots = [
        ClaimSnapshot(before_ns=t, after_ns=t + 1, boot_id="b", claims=[fact])
        for t in range(0, 2_200_000_000, 50_000_000)
    ]
    window.snapshots = snapshots
    validate_snapshots(window, load_ready_ns=1_000_000_000)
    snapshots[-1].claims[0].lease_live = False
    with pytest.raises(ValueError, match="lease"):
        validate_snapshots(window, load_ready_ns=1_000_000_000)


@pytest.mark.asyncio
async def test_snapshot_retains_real_before_after_brackets_even_when_sql_predicate_fails(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.observers import RecoveryJournal

    class Facts:
        async def active(self, runs):
            return []

        async def batch(self, batch, *, counts):
            return {
                "status": "running",
                "settings": {},
                "sends": 1,
                "settled": 0,
                "active_call_ids": ["physical"],
            }

    stamps = iter([10, 20, 30, 40])
    monkeypatch.setattr(
        "scripts.execution_capacity.live_runtime.time.monotonic_ns", lambda: next(stamps)
    )
    workload = object.__new__(LiveWorkload)
    workload.facts, workload.runs, workload.batch_id, workload.boot = Facts(), [], "batch", "boot"
    workload.bridge, workload.defaults = SimpleNamespace(key="window"), {}
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        workload.journal = journal
        with pytest.raises(ValueError, match="ten"):
            await workload.snapshot()
        rows = list(journal.records("live_claim_snapshot"))
        assert rows[0][1]["body"]["before_ns"] == 10
        assert rows[0][1]["body"]["after_ns"] == 20
        assert rows[0][1]["body"]["rows"] == []


def progress_fact():
    return {
        "run_id": "run",
        "activity_id": "activity",
        "generation": 1,
        "claim_generation": 2,
        "boot_id": "boot",
        "pid": 8,
        "marker_id": "marker",
        "marker_sequence": 1,
        "marker_captured_ns": 1,
        "event_id": "event",
        "sequence": 3,
        "before_ns": 2,
        "after_ns": 4,
        "ack": False,
        "error": "sink",
        "message": "Received fragments: 3",
        "source_identity": "event",
        "source": {
            "activity_id": "activity",
            "generation": 1,
            "claim_generation": 2,
            "sequence": 3,
        },
        "applied": True,
        "observed_order": 4,
        "projection_revision": 5,
        "public_event_id": "event",
        "public_run_id": "run",
        "public_message": "Received fragments: 3",
    }


def test_progress_export_requires_actual_sql_identity_and_preserves_failed_ack():
    from scripts.execution_capacity.source_export import export_progress

    row = progress_fact()
    exported = export_progress(row, window_id="window", phase="measured")
    assert exported.ack is False
    assert exported.error == "sink"
    del row["source_identity"]
    with pytest.raises((KeyError, ValueError)):
        export_progress(row, window_id="window", phase="measured")


def test_actual_journal_source_export_keeps_snapshot_join_and_pending_cleanup(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    from scripts.execution_capacity.guest_state import GuestState
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.source_export import source_shard

    monkeypatch.setattr(
        "scripts.execution_capacity.guest_state.process_snapshot", lambda pid: {"pid": pid}
    )
    identity = {
        k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")
    }
    identity["source_digest"] = "a" * 64
    (tmp_path / "state").mkdir(mode=0o700)
    (tmp_path / "journal").mkdir(mode=0o700)
    state = GuestState(tmp_path / "state", identity)
    try:
        state.initialize()
        state.publish("minimal_ready", {"guest_ns": 1})
        state.publish(
            "measurement",
            {
                "rule": {"start_offset_ns": 0, "end_offset_ns": 5, "control_margin_ns": 1},
                "start_ns": 10,
                "end_ns": 20,
            },
        )
        state.establish(
            sessions={f"s{i}": f"r{i}" for i in range(10)},
            claims={(f"r{i}", f"a{i}", 1, 1, f"c{i}") for i in range(10)},
            batch_id="batch",
            start_ns=10,
            end_ns=20,
            snapshot_id="snap",
        )
        state.publish("client_ready", {"guest_ns": 9, "native_digest": "b" * 64})
        state.publish("client_done", {"guest_ns": 19, "native_digest": "c" * 64})
        state.publish("measurement_closed", {"guest_ns": 15, "feed_cursor": 0})
        state.publish("source_settled", {"guest_ns": 21, "host_physical_cleanup": "pending_C"})
        state.publish(
            "source_result", {"body": {"start_ns": 10, "end_ns": 20}, "receipt": {"updates": []}}
        )
        with RecoveryJournal(tmp_path / "journal") as journal:
            facts = [
                {
                    "run_id": f"r{i}",
                    "activity_id": f"a{i}",
                    "generation": 1,
                    "claim_generation": 1,
                    "call_identity": f"c{i}",
                    "reservation_id": f"c{i}",
                    "policy_id": "policy",
                    "claimed_by": "worker",
                    "sql_observed_at": "2026-09-18T00:00:10Z",
                    "call_started_at": "2026-09-18T00:00:01Z",
                    "heartbeat_at": "2026-09-18T00:00:09Z",
                    "claim_deadline": "2026-09-18T00:01:00Z",
                    "timeout_at": "2026-09-18T00:01:00Z",
                    "lease_live": True,
                    "settled": False,
                    "terminal": False,
                    "reservation_state": "dispatching",
                    "status": "call_started",
                    "configured_model": "acceptance-live",
                    "stream": True,
                }
                for i in range(10)
            ]
            journal.intent(
                "live_claim_snapshot",
                "snap",
                {
                    "window_id": identity["window_id"],
                    "boot_id": identity["boot_id"],
                    "before_ns": 7,
                    "after_ns": 8,
                    "rows": facts,
                },
            )
            journal.intent(
                "live_batch_snapshot",
                "snap",
                {
                    "window_id": identity["window_id"],
                    "boot_id": identity["boot_id"],
                    "before_ns": 8,
                    "after_ns": 9,
                    "batch_id": "batch",
                    "suite_version": "suite",
                    "batch": {
                        "status": "running",
                        "settings": {
                            "subject_concurrency": 5,
                            "judge_concurrency": 2,
                            "environment_concurrency": 5,
                        },
                        "sends": 3,
                        "settled": 1,
                        "active_call_ids": ["call"],
                    },
                },
            )
            journal.intent("live_batch", "batch", {"batch_results": 5000})
            page = source_shard(state, journal, "metadata", 0)
            assert page.rows[0].cleanup == "pending_C"
            assert page.rows[0].claims[0].call_identity == "c0"
            assert page.rows[0].batch_results == 5000
            snap = source_shard(state, journal, "snapshots", 0).rows[0]
            assert (snap.before_ns, snap.after_ns) == (7, 8)
            assert snap.claims[0].heartbeat_at == "2026-09-18T00:00:09Z"
            with pytest.raises(ValueError, match="missing"):
                source_shard(state, journal, "progress", 0)
            with pytest.raises(ValueError, match="outside"):
                source_shard(state, journal, "metadata", 1)
    finally:
        state.close()


def test_missing_bridge_progress_receipt_never_invents_ack_clock(tmp_path):
    from types import SimpleNamespace

    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.observers import RecoveryJournal

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent(
            "live_progress",
            "event",
            {"run_id": "r", "boot_id": "b", "phase": "model_response", "before_ns": 12},
        )
        workload = object.__new__(LiveWorkload)
        workload.journal, workload.runs, workload.boot, workload.bridge = (
            journal,
            ["r"],
            "b",
            SimpleNamespace(),
        )
        row = workload.progress_rows()[0]
        assert row["ack"] is False
        assert row["after_ns"] is None
        assert row["error"] == "missing-persistence-receipt"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lease_live", False),
        ("settled", True),
        ("terminal", True),
        ("reservation_id", "other"),
        ("heartbeat_at", "2026-09-18T00:01:01Z"),
        ("claim_deadline", "2026-09-18T00:00:05Z"),
        ("sql_observed_at", "2026-09-18T00:00:10"),
        ("policy_id", "other"),
        ("claim_generation", 2),
    ],
)
def test_invalid_sql_predicate_or_identity_cannot_count_as_continuity(field, value):
    from types import SimpleNamespace

    from scripts.acceptance.capacity_models import Claim, ClaimSnapshot
    from scripts.acceptance.capacity_timing import validate_snapshots

    claim = Claim(
        run_id="r",
        activity_id="a",
        generation=1,
        claim_generation=1,
        call_identity="c",
        session_id="s",
        policy_id="p",
        configured_model="acceptance-live",
        stream=True,
        boot_id="b",
    )
    fact = {
        "run_id": "r",
        "activity_id": "a",
        "generation": 1,
        "claim_generation": 1,
        "call_identity": "c",
        "policy_id": "p",
        "reservation_id": "c",
        "claimed_by": "worker",
        "sql_observed_at": "2026-09-18T00:00:10Z",
        "call_started_at": "2026-09-18T00:00:01Z",
        "heartbeat_at": "2026-09-18T00:00:09Z",
        "claim_deadline": "2026-09-18T00:00:20Z",
        "timeout_at": "2026-09-18T00:01:00Z",
        "lease_live": True,
        "settled": False,
        "terminal": False,
        "reservation_state": "dispatching",
        "status": "call_started",
        "configured_model": "acceptance-live",
        "stream": True,
    }
    snapshots = [
        ClaimSnapshot(before_ns=t, after_ns=t, boot_id="b", claims=[fact])
        for t in (0, 50_000_000, 100_000_000)
    ]
    window = SimpleNamespace(
        boot_id="b",
        guest_start_ns=50_000_000,
        guest_end_ns=100_000_000,
        claims=[claim],
        snapshots=snapshots,
    )
    setattr(snapshots[1].claims[0], field, value)
    with pytest.raises(ValueError, match=r"SQL|claim|timezone"):
        validate_snapshots(window, load_ready_ns=50_000_000)


def test_calibration_export_rejects_shrunken_fixed_transfer():
    from scripts.execution_capacity.reference_calibration import export_probes

    rows = [
        {
            "action": "echo",
            "start_ns": i * 10 + 1,
            "end_ns": i * 10 + 11,
            "elapsed_ns": 10,
            "bytes": 32,
            "bits_per_second": 25_600_000_000,
            "echo_rtt_ns": 10,
        }
        for i in range(16)
    ]
    rows += [
        {
            "action": action,
            "start_ns": i * 10 + 1,
            "end_ns": i * 10 + 11,
            "elapsed_ns": 10,
            "bytes": 32,
            "bits_per_second": 25_600_000_000,
            "echo_rtt_ns": None,
        }
        for i, action in enumerate(("upload", "download"), 16)
    ]
    observation = {
        "identity": {"window_id": "w"},
        "phase": "window",
        "measurements": {"rows": rows, "errors": [], "attempted": 18, "expected": 18},
    }
    with pytest.raises(ValueError, match=r"fixed.*bytes"):
        export_probes(observation, clock_id="clock")
