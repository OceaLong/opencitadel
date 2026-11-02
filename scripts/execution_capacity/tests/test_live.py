"""Bounded live evidence validation, never runtime capacity proof."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest


def topology():
    return SimpleNamespace(
        execution_activity_max_concurrency=8,
        execution_activity_batch_size=8,
        postgres_pool_size=5,
        postgres_max_overflow=5,
        evaluation_subject_concurrency=5,
        evaluation_judge_concurrency=2,
        evaluation_environment_concurrency=2,
        physical_global_concurrency=30,
        physical_user_concurrency=30,
        physical_provider_concurrency=30,
    )


def test_topology_requires_real_handler_and_pool_headroom_without_raising_limits():
    from scripts.execution_capacity.live import validate_topology

    settings = topology()
    assert validate_topology(settings, 3)["required_handlers"] == 18
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        validate_topology(settings, 1)
    settings.execution_activity_batch_size = 100
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        validate_topology(settings, 3)
    settings.execution_activity_batch_size = 8
    settings.physical_user_concurrency = 10
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        validate_topology(settings, 3)


def rows():
    return [
        {
            "run_id": str(i),
            "event_id": str(uuid4()),
            "sequence": j + 1,
            "activity_id": str(i),
            "claim_generation": 1,
            "generation": 0,
            "ack": True,
            "before_ns": int((j * 0.5 + 0.1) * 1e9),
            "after_ns": int((j * 0.5 + 0.2) * 1e9),
            "effective": True,
            "public": True,
            "message": f"Received fragments: {j + 1}",
        }
        for i in range(10)
        for j in range(4)
    ]


def test_rate_counts_only_distinct_committed_effective_public_updates():
    from scripts.execution_capacity.live import validate_updates

    valid = rows()
    assert validate_updates(valid, [str(i) for i in range(10)], 0, 2_000_000_000)["updates"] == 40
    for mutate in [
        lambda r: r.pop(),
        lambda r: r[0].update(ack=False),
        lambda r: r[0].update(effective=False),
        lambda r: r[0].update(public=False),
        lambda r: r[0].update(event_id=r[1]["event_id"]),
        lambda r: r[0].update(sequence=r[1]["sequence"]),
        lambda r: r[0].update(message=r[1]["message"]),
    ]:
        changed = rows()
        mutate(changed)
        with pytest.raises(
            ValueError,
            match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
        ):
            validate_updates(changed, [str(i) for i in range(10)], 0, 2_000_000_000)


def test_progress_observer_preserves_failed_ack_and_exception(tmp_path):
    from datetime import UTC, datetime

    from scripts.execution_capacity.live import ObservedProgress
    from scripts.execution_capacity.observers import RecoveryJournal

    from app.application.execution.progress import ActivityProgressRecord

    record = ActivityProgressRecord(
        run_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        claim_generation=1,
        sequence=1,
        owner_user_id="u",
        team_id=None,
        occurred_at=datetime.now(UTC),
        kind="step",
        phase="model_response",
        progress=0,
        message="Received fragments: 1",
    )

    class Sink:
        async def record(self, item):
            assert item is record
            return False

    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        observer = ObservedProgress(Sink(), journal, boot_id="test-boot")
        assert asyncio.run(observer.record(record)) is False
        entries = list(journal.records("live_progress"))
        assert len(entries) == 1
        assert entries[0][1]["receipt"]["ack"] is False
        assert entries[0][1]["body"]["event_id"] == str(record.event_id)


def test_active_requires_ten_actual_started_physical_sends_not_claim_waiters():
    from scripts.execution_capacity.live_facts import validate_active

    rows = [
        {
            "run_id": str(i),
            "activity_id": str(i),
            "generation": 0,
            "claim_generation": 1,
            "call_identity": str(i),
            "status": "call_started",
            "lease_live": True,
            "reservation_state": "dispatching",
            "settled": False,
            "configured_model": "acceptance-live",
            "stream": True,
        }
        for i in range(10)
    ]
    assert len(validate_active(rows, [str(i) for i in range(10)])) == 10
    rows[0]["status"] = "claimed"
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        validate_active(rows, [str(i) for i in range(10)])
    rows[0]["status"] = "call_started"
    rows[0]["settled"] = True
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        validate_active(rows, [str(i) for i in range(10)])


def test_progress_readback_is_scoped_to_current_cohort(tmp_path):
    from scripts.execution_capacity.live import progress_records
    from scripts.execution_capacity.observers import RecoveryJournal

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("live_progress", "old", {"run_id": "previous"})
        journal.intent("live_progress", "current", {"run_id": "current"})
        journal.acknowledge("live_progress", "current", {"ack": False})
        values = list(progress_records(journal, ["current"]))
        assert len(values) == 1
        assert values[0]["body"]["run_id"] == "current"
        assert values[0]["receipt"]["ack"] is False
