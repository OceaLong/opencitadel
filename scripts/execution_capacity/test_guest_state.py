"""Private temporary rendezvous tests; production boundaries never run."""

from uuid import uuid4

import pytest
from scripts.execution_capacity.guest_state import GuestState, read_marker


@pytest.fixture(autouse=True)
def fake_process_boundary(monkeypatch):
    monkeypatch.setattr(
        "scripts.execution_capacity.guest_state.process_snapshot",
        lambda pid: {"pid": pid, "start_ticks": 42, "cgroup": "owned"},
    )


def identity():
    return {
        **{k: str(uuid4()) for k in ("attempt_id", "sample_id", "window_id", "nonce", "boot_id")},
        "source_digest": "a" * 64,
    }


def test_marker_atomic_order_and_foreign_boot(tmp_path):
    tmp_path.chmod(0o700)
    state = GuestState(tmp_path, identity())
    try:
        state.initialize()
        first = state.stamp(str(uuid4()), 1)
        assert read_marker(tmp_path, boot_id=state.identity["boot_id"]) == first
        with pytest.raises(ValueError, match="stale"):
            state.stamp(str(uuid4()), 1)
        with pytest.raises(ValueError, match="foreign"):
            read_marker(tmp_path, boot_id=str(uuid4()))
        second = state.stamp(str(uuid4()), 2)
        assert second["installed_ns"] >= first["installed_ns"]
        assert len(list(state.journal.records("bridge_marker_history"))) == 2
    finally:
        state.close()


def test_native_ready_is_tied_to_original_cohort_deadline(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    state = GuestState(tmp_path, identity())
    try:
        state.initialize()
        monkeypatch.setattr("scripts.execution_capacity.guest_state.time.monotonic_ns", lambda: 10)
        state.establish(sessions={}, claims=set(), batch_id=str(uuid4()), start_ns=100, end_ns=200)
        cohort = state.read("cohort")
        with pytest.raises(ValueError, match="foreign"):
            state.client_ready("b" * 64, "c" * 64)
        state.client_ready(cohort["cohort_digest"], "c" * 64)
        state.stamp(str(uuid4()), 1)
        assert state.require_ready()["guest_ns"] == 10
        monkeypatch.setattr("scripts.execution_capacity.guest_state.time.monotonic_ns", lambda: 100)
        with pytest.raises(ValueError, match="late"):
            state.client_ready(cohort["cohort_digest"], "c" * 64)
    finally:
        state.close()


def test_client_done_requires_open_and_original_end(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    state = GuestState(tmp_path, identity())
    try:
        state.initialize()
        monkeypatch.setattr("scripts.execution_capacity.guest_state.time.monotonic_ns", lambda: 100)
        with pytest.raises(ValueError, match="outside"):
            state.client_done("c" * 64)
        state.publish("running", {"start_ns": 90, "end_ns": 110})
        state.client_done("c" * 64)
        assert state.require_done()["guest_ns"] == 100
        monkeypatch.setattr("scripts.execution_capacity.guest_state.time.monotonic_ns", lambda: 111)
        with pytest.raises(ValueError, match="outside"):
            state.client_done("c" * 64)
    finally:
        state.close()


def test_observer_captures_marker_before_unchanged_sink(tmp_path):
    import asyncio
    from datetime import UTC, datetime

    from scripts.execution_capacity.live import ObservedProgress
    from scripts.execution_capacity.observers import RecoveryJournal

    from app.application.execution.progress import ActivityProgressRecord

    root = tmp_path / "bridge"
    root.mkdir(mode=0o700)
    journal_root = tmp_path / "worker"
    journal_root.mkdir(mode=0o700)
    state = GuestState(root, identity())
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
    try:
        state.initialize()
        first = state.stamp(str(uuid4()), 1)

        class Sink:
            async def record(self, item):
                assert item is record
                state.stamp(str(uuid4()), 2)
                return True

        with RecoveryJournal(journal_root) as journal:
            observer = ObservedProgress(
                Sink(), journal, boot_id=state.identity["boot_id"], marker_root=root
            )
            assert asyncio.run(observer.record(record)) is True
            body = next(iter(journal.records("live_progress")))[1]["body"]
            assert body["marker_id"] == first["marker_id"]
            assert body["marker_captured_ns"] <= body["before_ns"]
    finally:
        state.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("bridge_enabled", [True, False])
async def test_real_window_publishes_cohort_before_deadline_and_always_finishes(
    tmp_path, monkeypatch, ready, bridge_enabled
):
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity import live_runtime
    from scripts.execution_capacity.observers import RecoveryJournal

    clock = [0]
    original_sleep = asyncio.sleep
    sleeps = []

    async def advance(seconds):
        sleeps.append(seconds)
        clock[0] += int(seconds * 1e9)
        await original_sleep(0)

    monkeypatch.setattr(live_runtime.time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setattr(live_runtime.asyncio, "sleep", advance)
    published = []

    class Bridge:
        key = "window"

        def publish(self, kind, body):
            pass

        def establish(self, **body):
            published.append((clock[0], body))

        def require_ready(self):
            if not ready:
                raise ValueError("missing native ready")

    class Workload(live_runtime.LiveWorkload):
        async def publish_incremental(self):
            pass

        async def verify_policies(self):
            pass

        async def admit(self, session, identity, **kwargs):
            self.sessions.append(session["session_id"])
            self.runs.append("run-" + session["session_id"])

        async def start_batch(self, window_id):
            self.batch_id = uuid4()

        async def snapshot(self, **kwargs):
            self.last_snapshot_id = "observed-snapshot"
            return {(r, r + "-activity", 0, 1, r + "-call") for r in self.runs}, {}

        async def finish(self):
            self.finished = True

    tmp_path.chmod(0o700)
    window_id = uuid4()
    with RecoveryJournal(tmp_path) as journal:
        workload = object.__new__(Workload)
        workload.live = {
            "windows": {
                str(window_id): {
                    "startup_seconds": 3,
                    "seconds": 4,
                    "measurement": {
                        "start_offset_ns": 0,
                        "end_offset_ns": 1_000_000_000,
                        "control_margin_ns": 1_000_000_000,
                    },
                    "sessions": [{"session_id": str(i)} for i in range(10)],
                }
            }
        }
        workload.journal, workload.bridge = journal, Bridge() if bridge_enabled else None
        workload.boot, workload.binding = "boot", {"source_sha256": "a" * 64}
        workload.sessions, workload.runs, workload.finished = [], [], False
        workload.settings = SimpleNamespace()

        async def execute_window():
            async with workload.window(window_id, minimal_ready_ns=0):
                assert ready or not bridge_enabled
                raise ValueError("native work failed")

        with pytest.raises(ValueError, match=r"native work|native ready"):
            await execute_window()
        if bridge_enabled:
            assert published[0][0] < published[0][1]["start_ns"]
            assert len(published[0][1]["sessions"]) == 10
        else:
            assert published == []
        assert max(sleeps) == (0.05 if bridge_enabled else 0.1)
        assert workload.finished


def test_process_reuse_is_not_live_helper(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    state = GuestState(tmp_path, identity())
    try:
        state.initialize()
        assert state.process_observation()["same_process"] is True
        monkeypatch.setattr(
            "scripts.execution_capacity.guest_state.process_snapshot",
            lambda pid: {"pid": pid, "start_ticks": 43, "cgroup": "owned"},
        )
        result = state.process_observation()
        assert result["original"]["start_ticks"] == 42
        assert result["current"]["start_ticks"] == 43
        assert result["same_process"] is False
    finally:
        state.close()
