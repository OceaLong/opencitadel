"""The execution kernel must keep polling PostgreSQL when Redis hints fail."""

import json
import os
from types import SimpleNamespace

import pytest

import app.execution_kernel_main as kernel_main
from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import WakeupBatch
from app.execution_kernel_main import ExecutionKernelProcess


class _FailingWakeup:
    def __init__(self) -> None:
        self.calls = 0

    async def read(self, cursor, *, block_milliseconds):
        del block_milliseconds
        self.calls += 1
        return WakeupBatch(
            cursor=cursor,
            messages=(),
            connectivity=RedisConnectivity(False, "redis_unavailable"),
        )


class _ReadyPolicyReader:
    async def refresh_if_due(self, *, now) -> None:
        del now

    @staticmethod
    def readiness():
        return SimpleNamespace(ready=True, error_key=None)


class _IdleRuntime:
    activity_registry = SimpleNamespace(registered_types=())

    def __init__(self, stopping) -> None:
        self._stopping = stopping
        self.inbox_calls = 0

    async def run_pending_projectors_once(self, **_kwargs):
        return SimpleNamespace(processed=0)

    async def run_inbox_once(self, **_kwargs):
        self.inbox_calls += 1
        if self.inbox_calls == 2:
            self._stopping.set()
            return SimpleNamespace(loaded=1)
        return SimpleNamespace(loaded=0)

    async def run_decisions_once(self, **_kwargs):
        return SimpleNamespace(submitted=0)

    async def run_activities_once(self, **_kwargs):
        return SimpleNamespace(claimed=0)

    async def run_timers_once(self, **_kwargs):
        return SimpleNamespace(fired=0)

    async def run_outbox_once(self, **_kwargs):
        return SimpleNamespace(claimed=0)


class _BlockingRuntime(_IdleRuntime):
    def __init__(self, stopping, started, release) -> None:
        super().__init__(stopping)
        self._started = started
        self._release = release

    async def run_pending_projectors_once(self, **_kwargs):
        self._started.set()
        await self._release.wait()
        return SimpleNamespace(processed=0)


class _BlockingActivityRuntime(_IdleRuntime):
    def __init__(self, stopping, activity_started, control_progressed) -> None:
        super().__init__(stopping)
        self._activity_started = activity_started
        self._control_progressed = control_progressed

    async def run_inbox_once(self, **_kwargs):
        self.inbox_calls += 1
        if self._activity_started.is_set():
            self._control_progressed.set()
            self._stopping.set()
            return SimpleNamespace(loaded=1)
        return SimpleNamespace(loaded=0)

    async def run_activities_once(self, **_kwargs):
        self._activity_started.set()
        await self._stopping.wait()
        return SimpleNamespace(claimed=1)


@pytest.mark.asyncio
async def test_kernel_keeps_polling_database_when_redis_hint_read_fails():
    """Regression: letting ConnectionError escape stops all durable work."""
    import asyncio

    stopping = asyncio.Event()
    runtime = _IdleRuntime(stopping)
    wakeup = _FailingWakeup()
    process = ExecutionKernelProcess(
        runtime=runtime,
        wakeup=wakeup,
        policy_reader=_ReadyPolicyReader(),
        stopping=stopping,
        idle_poll_seconds=0,
    )

    await process.run()

    assert runtime.inbox_calls == 2
    assert wakeup.calls == 1


@pytest.mark.asyncio
async def test_kernel_refreshes_a_process_local_liveness_heartbeat(tmp_path):
    import asyncio

    stopping = asyncio.Event()
    runtime = _IdleRuntime(stopping)
    health_file = tmp_path / "execution-kernel.health"
    process = ExecutionKernelProcess(
        runtime=runtime,
        wakeup=_FailingWakeup(),
        policy_reader=_ReadyPolicyReader(),
        stopping=stopping,
        idle_poll_seconds=0,
        health_file=health_file,
    )
    written = asyncio.Event()
    refresh_heartbeat = process._refresh_heartbeat

    def refresh_and_signal() -> None:
        refresh_heartbeat()
        written.set()

    process._refresh_heartbeat = refresh_and_signal

    heartbeat = asyncio.create_task(process.run_heartbeat())
    await written.wait()

    marker = json.loads(health_file.read_text(encoding="utf-8"))
    assert marker["pid"] == os.getpid()
    assert isinstance(marker["updated_at_epoch"], float)
    assert marker["runtime_policy_ready"] is True

    stopping.set()
    await heartbeat
    assert not health_file.exists()


@pytest.mark.asyncio
async def test_kernel_heartbeat_continues_during_long_async_work(tmp_path):
    import asyncio

    stopping = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()
    health_file = tmp_path / "execution-kernel.health"
    runtime = _BlockingRuntime(stopping, started, release)
    process = ExecutionKernelProcess(
        runtime=runtime,
        wakeup=_FailingWakeup(),
        policy_reader=_ReadyPolicyReader(),
        stopping=stopping,
        health_file=health_file,
        heartbeat_interval_seconds=0.005,
    )

    running = asyncio.create_task(process.run())
    heartbeat = asyncio.create_task(process.run_heartbeat())
    await started.wait()
    first = json.loads(health_file.read_text(encoding="utf-8"))["updated_at_epoch"]
    await asyncio.sleep(0.02)
    second = json.loads(health_file.read_text(encoding="utf-8"))["updated_at_epoch"]
    stopping.set()
    release.set()
    await running
    await heartbeat

    assert second > first
    assert not health_file.exists()
    assert not hasattr(process, "_heartbeat_task")


@pytest.mark.asyncio
async def test_long_activity_does_not_block_control_plane_progress() -> None:
    import asyncio

    stopping = asyncio.Event()
    activity_started = asyncio.Event()
    control_progressed = asyncio.Event()
    runtime = _BlockingActivityRuntime(stopping, activity_started, control_progressed)
    process = ExecutionKernelProcess(
        runtime=runtime,
        wakeup=_FailingWakeup(),
        policy_reader=_ReadyPolicyReader(),
        stopping=stopping,
        idle_poll_seconds=0,
    )

    running = asyncio.create_task(process.run())
    await activity_started.wait()
    await asyncio.wait_for(control_progressed.wait(), timeout=0.1)
    await asyncio.wait_for(running, timeout=0.1)

    assert runtime.inbox_calls >= 2


@pytest.mark.asyncio
async def test_run_kernel_uses_typed_runtime_and_supervisor(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    from app.composition.tasks import TaskSupervisor
    from core.config import DeploymentSettings

    observed: list[object] = []
    supervisor = TaskSupervisor()
    execution = object()
    wakeup = object()
    policy_reader = _ReadyPolicyReader()
    runtime = SimpleNamespace(
        supervisor=supervisor,
        execution=execution,
        wakeup=wakeup,
        policy_reader=policy_reader,
    )

    @asynccontextmanager
    async def runtime_factory(settings):
        observed.append(("open", settings))
        try:
            yield runtime
        finally:
            await supervisor.stop()
            observed.append("closed")

    class _ImmediateProcess:
        def __init__(self, *, runtime, wakeup, policy_reader, stopping):
            observed.append(("process", runtime, wakeup, policy_reader))
            self._stopping = stopping

        async def run(self):
            observed.append("run")
            self._stopping.set()
            await asyncio.sleep(0)

        async def run_heartbeat(self):
            observed.append("heartbeat")
            await self._stopping.wait()

    monkeypatch.setattr(kernel_main, "ExecutionKernelProcess", _ImmediateProcess)
    monkeypatch.setattr(
        kernel_main,
        "install_signal_handlers",
        lambda request_stop: observed.append(("signals", request_stop)),
    )
    settings = DeploymentSettings(env="test")

    await kernel_main.run_kernel(settings, runtime_factory=runtime_factory)

    assert observed[0] == ("open", settings)
    assert observed[1] == ("process", execution, wakeup, policy_reader)
    assert observed[2] == ("signals", supervisor.request_stop)
    assert set(observed[3:-1]) == {"run", "heartbeat"}
    assert observed[-1] == "closed"
    assert supervisor.pending_names == ()
