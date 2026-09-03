"""The execution kernel must keep polling PostgreSQL when Redis hints fail."""

import json
import os
from types import SimpleNamespace

import pytest

import app.execution_kernel_main as kernel_main
from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import WakeupBatch
from app.execution_kernel_main import ExecutionKernelProcess
from core.config import DeploymentSettings


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


class _FailingLaneRuntime(_IdleRuntime):
    def __init__(self, stopping) -> None:
        super().__init__(stopping)
        self.decision_calls = 0

    async def run_decisions_once(self, **_kwargs):
        self.decision_calls += 1
        raise RuntimeError("poison decision lane")


@pytest.mark.asyncio
async def test_control_plane_survives_a_single_lane_failure():
    """A raising lane must not tear down the whole control-plane process."""
    import asyncio

    stopping = asyncio.Event()
    runtime = _FailingLaneRuntime(stopping)
    process = ExecutionKernelProcess(
        runtime=runtime,
        wakeup=_FailingWakeup(),
        policy_reader=_ReadyPolicyReader(),
        stopping=stopping,
        idle_poll_seconds=0,
    )

    # Returns normally (no propagated exception) despite every decision lane
    # raising, and the inbox lane keeps advancing across iterations.
    await process.run()

    assert runtime.decision_calls >= 2
    assert runtime.inbox_calls == 2


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
        def __init__(
            self, *, runtime, wakeup, policy_reader, stopping, batch_size, idle_poll_seconds
        ):
            observed.append(
                ("process", runtime, wakeup, policy_reader, batch_size, idle_poll_seconds)
            )
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
    assert observed[1] == (
        "process",
        execution,
        wakeup,
        policy_reader,
        settings.execution_activity_batch_size,
        settings.execution_idle_poll_seconds,
    )
    assert observed[2] == ("signals", supervisor.request_stop)
    assert set(observed[3:-1]) == {"run", "heartbeat"}
    assert observed[-1] == "closed"
    assert supervisor.pending_names == ()


class _CaptureStart:
    def __init__(self):
        self.calls = []

    def __call__(self, environ, start_response):
        self.calls.append((environ, start_response))
        start_response("200 OK", [])
        return [b"# metrics\n"]


def _wsgi_environ(auth: str | None):
    environ = {"HTTP_HOST": "localhost"}
    if auth is not None:
        environ["HTTP_AUTHORIZATION"] = auth
    return environ


def test_token_guarded_metrics_app_rejects_missing_token(monkeypatch):
    inner = _CaptureStart()
    monkeypatch.setattr(kernel_main, "make_wsgi_app", lambda *a, **k: inner, raising=False)
    import prometheus_client

    monkeypatch.setattr(prometheus_client, "make_wsgi_app", lambda *a, **k: inner)

    app = kernel_main._token_guarded_metrics_app("s3cret")
    statuses = []
    body = app(_wsgi_environ(None), lambda status, headers: statuses.append(status))
    assert statuses == ["401 Unauthorized"]
    assert b"Unauthorized" in b"".join(body)
    assert inner.calls == []


def test_token_guarded_metrics_app_rejects_wrong_token(monkeypatch):
    inner = _CaptureStart()
    import prometheus_client

    monkeypatch.setattr(prometheus_client, "make_wsgi_app", lambda *a, **k: inner)

    app = kernel_main._token_guarded_metrics_app("s3cret")
    statuses = []
    app(_wsgi_environ("Bearer wrong"), lambda status, headers: statuses.append(status))
    assert statuses == ["401 Unauthorized"]
    assert inner.calls == []


def test_token_guarded_metrics_app_passes_correct_token(monkeypatch):
    inner = _CaptureStart()
    import prometheus_client

    monkeypatch.setattr(prometheus_client, "make_wsgi_app", lambda *a, **k: inner)

    app = kernel_main._token_guarded_metrics_app("s3cret")
    statuses = []
    app(_wsgi_environ("Bearer s3cret"), lambda status, headers: statuses.append(status))
    assert statuses == ["200 OK"]
    assert len(inner.calls) == 1


def test_start_kernel_metrics_server_disabled_when_port_zero(monkeypatch):
    called = []
    import prometheus_client

    monkeypatch.setattr(
        prometheus_client, "start_http_server", lambda *a, **k: called.append(("http", a, k))
    )
    kernel_main.start_kernel_metrics_server(DeploymentSettings(execution_kernel_metrics_port=0))
    assert called == []


def test_start_kernel_metrics_server_loopback_without_token(monkeypatch):
    called = []
    import prometheus_client

    monkeypatch.setattr(
        prometheus_client, "start_http_server", lambda *a, **k: called.append((a, k))
    )
    kernel_main.start_kernel_metrics_server(
        DeploymentSettings(execution_kernel_metrics_port=9108, metrics_token="")
    )
    assert called == [((9108,), {"addr": "127.0.0.1"})]


def test_start_kernel_metrics_server_token_protected(monkeypatch):
    served = []

    class _FakeServer:
        def serve_forever(self):
            pass

    def _fake_make_server(host, port, app, handler_class):
        served.append((host, port, app, handler_class))
        return _FakeServer()

    started_threads = []

    class _FakeThread:
        def __init__(self, *, target, name, daemon):
            started_threads.append((target, name, daemon))

        def start(self):
            pass

    monkeypatch.setattr(kernel_main, "make_server", _fake_make_server, raising=False)
    import wsgiref.simple_server as simple_server

    monkeypatch.setattr(simple_server, "make_server", _fake_make_server)
    monkeypatch.setattr("threading.Thread", _FakeThread)

    kernel_main.start_kernel_metrics_server(
        DeploymentSettings(execution_kernel_metrics_port=9108, metrics_token="s3cret")
    )
    assert len(served) == 1
    assert served[0][0] == "0.0.0.0"
    assert served[0][1] == 9108
    assert len(started_threads) == 1
    assert started_threads[0][1] == "kernel-metrics-server"
    assert started_threads[0][2] is True


@pytest.mark.asyncio
async def test_main_wires_observability_and_metrics(monkeypatch):
    observed = {}

    monkeypatch.setattr(
        kernel_main, "load_deployment_settings", lambda: DeploymentSettings(env="test")
    )
    monkeypatch.setattr(
        kernel_main, "setup_logging", lambda settings: observed.setdefault("log", settings)
    )
    monkeypatch.setattr(
        kernel_main,
        "setup_observability",
        lambda *, settings: observed.setdefault("otel", settings),
    )
    monkeypatch.setattr(
        kernel_main,
        "start_kernel_metrics_server",
        lambda settings: observed.setdefault("metrics", settings),
    )

    async def _fake_run_kernel(settings):
        observed["ran"] = settings

    monkeypatch.setattr(kernel_main, "run_kernel", _fake_run_kernel)

    await kernel_main.main()

    assert "otel" in observed
    assert observed["otel"] is observed["log"]
    assert observed["metrics"] is observed["log"]
    assert observed["ran"] is observed["log"]
