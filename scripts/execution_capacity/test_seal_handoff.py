"""The seed stays alive until actual identity capture releases normal drain."""

import asyncio

import pytest


def test_handoff_release_cannot_replace_actual_child_identity(tmp_path):
    from scripts.execution_capacity.seal_handoff import publish, release, wait_release

    tmp_path.chmod(0o700)
    identity = {
        "attempt_id": "attempt",
        "boot_id": "boot",
        "source_digest": "a" * 64,
        "pid": 42,
        "start_ticks": 100,
        "pid_namespace": 12,
        "hostname": "actual-child",
    }
    publish(tmp_path, identity, deadline_ns=1000)
    with pytest.raises(ValueError, match="captured"):
        release(tmp_path, {**identity, "start_ticks": 101}, now_ns=900)
    release(tmp_path, identity, now_ns=900)
    asyncio.run(wait_release(tmp_path, identity, clock=lambda: 950))
    with pytest.raises(TimeoutError):
        asyncio.run(wait_release(tmp_path, identity, clock=lambda: 1001))
    assert (tmp_path / "seal-ready.json").exists()


def test_handoff_publishes_then_waits_for_capture_before_drain(tmp_path):
    from scripts.execution_capacity.seal_handoff import publish, release, wait_release

    tmp_path.chmod(0o700)
    events = []
    identity = {"pid": 42}

    async def scenario():
        publish(tmp_path, identity, deadline_ns=1000)
        events.append("ready")
        waiting = asyncio.create_task(wait_release(tmp_path, identity, clock=lambda: 950))
        await asyncio.sleep(0)
        assert not waiting.done()
        events.append("capture")
        release(tmp_path, identity, now_ns=900)
        await waiting
        events.extend(["drain", "exit"])

    asyncio.run(scenario())
    assert events == ["ready", "capture", "drain", "exit"]
