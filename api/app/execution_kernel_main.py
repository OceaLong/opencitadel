"""The only execution-kernel process."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path

from app.application.ports.streams import WakeupPort
from app.application.security.authorization_context import authorization_scope
from app.composition.kernel import open_kernel_runtime
from app.composition.tasks import TaskFailure, TaskKind
from app.composition.types import KernelRuntime
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.logging import setup_logging
from app.observability.otel import setup_observability
from core.config import DeploymentSettings, load_deployment_settings

logger = logging.getLogger(__name__)
KernelRuntimeFactory = Callable[..., AbstractAsyncContextManager[KernelRuntime]]


class ExecutionKernelProcess:
    def __init__(
        self,
        *,
        runtime,
        wakeup: WakeupPort,
        policy_reader,
        batch_size: int = 100,
        idle_poll_seconds: float = 1.0,
        stopping: asyncio.Event | None = None,
        health_file: Path | None = None,
        heartbeat_interval_seconds: float = 5.0,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if idle_poll_seconds < 0:
            raise ValueError("idle_poll_seconds must not be negative")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._runtime = runtime
        self._wakeup = wakeup
        self._policy_reader = policy_reader
        self._batch_size = batch_size
        self._idle_poll_seconds = idle_poll_seconds
        self._stopping = stopping or asyncio.Event()
        self._health_file = health_file or Path(
            os.environ.get(
                "EXECUTION_KERNEL_HEALTH_FILE",
                "/tmp/opencitadel-execution-kernel.health",
            )
        )
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._wakeup_cursor = "$"
        self._redis_hint_failed = False

    def request_shutdown(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info(
            "Execution kernel started: activities=%s",
            self._runtime.activity_registry.registered_types,
        )
        lanes = {
            asyncio.create_task(
                self._run_control_plane(),
                name="execution-kernel-control-plane",
            ),
            asyncio.create_task(
                self._run_activity_plane(),
                name="execution-kernel-activity-plane",
            ),
        }
        try:
            done, _ = await asyncio.wait(
                lanes,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
            if not self._stopping.is_set():
                raise RuntimeError("execution kernel lane stopped unexpectedly")
        finally:
            for task in lanes:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)

    async def _run_control_plane(self) -> None:
        while not self._stopping.is_set():
            now = datetime.now(UTC)
            try:
                await self._policy_reader.refresh_if_due(now=now)
            except Exception:
                logger.exception("execution kernel policy refresh failed")
            # Each lane is isolated: a single lane raising (e.g. one poison row
            # or a transient store error) must not tear down the whole control
            # plane process. Without this a CRITICAL, un-restarted kernel task
            # would exit and every replica would CrashLoopBackOff on the same
            # bad input. Failed lanes log and count as zero work.
            work = 0
            work += await self._run_lane(
                "projectors",
                self._runtime.run_pending_projectors_once(
                    scope_limit=self._batch_size,
                    event_limit=self._batch_size,
                ),
                "processed",
            )
            work += await self._run_lane(
                "inbox",
                self._runtime.run_inbox_once(limit=self._batch_size, now=now),
                "loaded",
            )
            work += await self._run_lane(
                "decisions",
                self._runtime.run_decisions_once(limit=self._batch_size, now=now),
                "submitted",
            )
            work += await self._run_lane(
                "timers",
                self._runtime.run_timers_once(limit=self._batch_size, now=now),
                "fired",
            )
            work += await self._run_lane(
                "outbox",
                self._runtime.run_outbox_once(limit=self._batch_size, now=now),
                "claimed",
            )
            if work == 0:
                try:
                    await self._wait_for_hint()
                except Exception:
                    logger.exception("execution kernel wake-up hint failed")

    async def _run_lane(self, name: str, work: object, attribute: str) -> int:
        """Await one control-plane lane, isolating failures from its peers."""
        try:
            result = await work  # type: ignore[misc]
        except Exception:
            logger.exception("execution kernel control-plane lane '%s' failed", name)
            return 0
        return int(getattr(result, attribute))

    async def _run_activity_plane(self) -> None:
        while not self._stopping.is_set():
            claimed = (
                await self._runtime.run_activities_once(
                    limit=self._batch_size,
                    now=datetime.now(UTC),
                )
            ).claimed
            if claimed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self._idle_poll_seconds,
                    )

    async def run_heartbeat(self) -> None:
        """Refresh the liveness marker under external task supervision."""

        try:
            self._refresh_heartbeat()
            while not self._stopping.is_set():
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self._heartbeat_interval_seconds,
                    )
                except TimeoutError:
                    self._refresh_heartbeat()
        finally:
            with contextlib.suppress(OSError):
                self._health_file.unlink(missing_ok=True)

    def _refresh_heartbeat(self) -> None:
        policy_readiness = self._policy_reader.readiness()
        marker = {
            "pid": os.getpid(),
            "updated_at_epoch": time.time(),
            "runtime_policy_ready": policy_readiness.ready,
            "runtime_policy_error_key": policy_readiness.error_key,
        }
        temporary = self._health_file.with_suffix(f"{self._health_file.suffix}.tmp")
        temporary.write_text(json.dumps(marker), encoding="utf-8")
        temporary.replace(self._health_file)

    async def _wait_for_hint(self) -> None:
        batch = await self._wakeup.read(
            self._wakeup_cursor,
            block_milliseconds=1000,
        )
        if not batch.connectivity.available:
            if not self._redis_hint_failed:
                logger.warning(
                    "Redis wake-up unavailable; continuing PostgreSQL polling: %s",
                    batch.connectivity.error_key,
                )
            self._redis_hint_failed = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._idle_poll_seconds,
                )
            return
        if self._redis_hint_failed:
            logger.info("Redis wake-up recovered")
            self._redis_hint_failed = False
        self._wakeup_cursor = batch.cursor


def install_signal_handlers(request_stop: Callable[[], None]) -> None:
    """Route process signals into the runtime's single shutdown event."""

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_stop)


async def _wait_for_stop_or_failure(runtime: KernelRuntime) -> TaskFailure | None:
    stop_waiter = asyncio.create_task(
        runtime.supervisor.stop_event.wait(),
        name="execution-kernel-stop-waiter",
    )
    failure_waiter = asyncio.create_task(
        runtime.supervisor.wait_for_critical_failure(),
        name="execution-kernel-failure-waiter",
    )
    try:
        done, _ = await asyncio.wait(
            (stop_waiter, failure_waiter),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if failure_waiter in done:
            return failure_waiter.result()
        return None
    finally:
        for waiter in (stop_waiter, failure_waiter):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(stop_waiter, failure_waiter, return_exceptions=True)


async def run_kernel(
    settings: DeploymentSettings,
    *,
    runtime_factory: KernelRuntimeFactory = open_kernel_runtime,
) -> None:
    """Run one explicitly composed kernel until a signal or critical failure."""

    with authorization_scope(AuthorizationContext.system("execution-kernel")):
        async with runtime_factory(settings) as runtime:
            process = ExecutionKernelProcess(
                runtime=runtime.execution,
                wakeup=runtime.wakeup,
                policy_reader=runtime.policy_reader,
                stopping=runtime.supervisor.stop_event,
                batch_size=settings.execution_activity_batch_size,
            )
            install_signal_handlers(runtime.supervisor.request_stop)
            await runtime.supervisor.start(
                "execution-kernel-heartbeat",
                process.run_heartbeat,
                kind=TaskKind.CRITICAL,
            )
            await runtime.supervisor.start(
                "execution-kernel",
                process.run,
                kind=TaskKind.CRITICAL,
            )
            failure = await _wait_for_stop_or_failure(runtime)
            if failure is not None:
                raise RuntimeError(
                    f"critical task[{failure.name}] failed after {failure.attempts} attempt(s)"
                ) from failure.error


def _token_guarded_metrics_app(token: str):
    """Wrap the Prometheus WSGI app with mandatory ``Bearer`` token auth.

    Mirrors ``/api/metrics`` (interfaces/endpoints/metrics_routes.py): a request
    without a matching ``Authorization: Bearer <token>`` header gets 401 and no
    metrics body. Returned as a plain WSGI callable so it can be unit-tested
    without binding a socket.
    """

    import hmac

    from prometheus_client import make_wsgi_app

    metrics_app = make_wsgi_app()

    def app(environ, start_response):
        header = environ.get("HTTP_AUTHORIZATION", "")
        scheme, _, provided = header.partition(" ")
        authorized = scheme.lower() == "bearer" and hmac.compare_digest(provided, token)
        if not authorized:
            start_response(
                "401 Unauthorized",
                [("Content-Type", "text/plain; charset=utf-8")],
            )
            return [b"Unauthorized\n"]
        return metrics_app(environ, start_response)

    return app


def start_kernel_metrics_server(settings: DeploymentSettings) -> None:
    """Expose kernel Prometheus metrics with the same guard posture as the API.

    The kernel has no FastAPI app, so it cannot reuse the ``/api/metrics`` route.
    With ``METRICS_TOKEN`` set the port serves metrics only to callers carrying a
    matching bearer token (consistent with ``/api/metrics``). With no token it
    would otherwise be an unauthenticated, network-reachable metrics endpoint, so
    it is bound to loopback instead — unreachable from the pod network.

    The primary production control for the metrics port is a NetworkPolicy that
    restricts ingress to :<port> to the Prometheus ``podSelector`` (that lives in
    the deploy manifests, out of this module's scope). The bearer token here and
    the loopback fallback are defense-in-depth on top of that netpol.
    """

    port = settings.execution_kernel_metrics_port
    if not port:
        return
    token = settings.metrics_token
    if token:
        from threading import Thread
        from wsgiref.simple_server import WSGIRequestHandler, make_server

        class _QuietHandler(WSGIRequestHandler):
            def log_message(self, *args: object) -> None:  # silence access logs
                return

        # Bound to all interfaces but gated by mandatory bearer-token auth above.
        httpd = make_server(
            "0.0.0.0",
            port,
            _token_guarded_metrics_app(token),
            handler_class=_QuietHandler,
        )
        Thread(
            target=httpd.serve_forever,
            name="kernel-metrics-server",
            daemon=True,
        ).start()
        logger.info("kernel metrics server started on :%s (bearer-token protected)", port)
        return

    from prometheus_client import start_http_server

    start_http_server(port, addr="127.0.0.1")
    logger.warning(
        "kernel metrics server bound to loopback only on :%s (METRICS_TOKEN unset); "
        "set METRICS_TOKEN to expose authenticated metrics to Prometheus",
        port,
    )


async def main() -> None:
    settings = load_deployment_settings()
    setup_logging(settings)
    setup_observability(settings=settings)
    start_kernel_metrics_server(settings)
    await run_kernel(settings)


if __name__ == "__main__":
    asyncio.run(main())


__all__ = [
    "ExecutionKernelProcess",
    "install_signal_handlers",
    "main",
    "run_kernel",
    "start_kernel_metrics_server",
]
