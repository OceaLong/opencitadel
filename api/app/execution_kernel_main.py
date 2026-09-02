"""Process entrypoint for the greenfield Effect/timer/retention kernel."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import signal
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path

from app.application.security.authorization_context import authorization_scope
from app.composition.kernel import open_kernel_runtime
from app.composition.tasks import TaskFailure, TaskKind
from app.composition.types import KernelRuntime
from app.contexts.kernel.runtime import KernelWorkerRuntime
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.logging import setup_logging
from app.observability.otel import setup_observability
from core.config import DeploymentSettings, load_deployment_settings

logger = logging.getLogger(__name__)
KernelRuntimeFactory = Callable[..., AbstractAsyncContextManager[KernelRuntime]]


class ExecutionKernelProcess:
    """Poll the three durable worker lanes; PostgreSQL remains authoritative."""

    def __init__(
        self,
        *,
        runtime: KernelWorkerRuntime,
        idle_poll_seconds: float = 1.0,
        stopping: asyncio.Event | None = None,
        health_file: Path | None = None,
        heartbeat_interval_seconds: float = 5.0,
    ) -> None:
        if idle_poll_seconds < 0:
            raise ValueError("idle_poll_seconds must not be negative")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._runtime = runtime
        self._idle_poll_seconds = idle_poll_seconds
        self._stopping = stopping or asyncio.Event()
        self._health_file = health_file or Path(
            os.environ.get(
                "EXECUTION_KERNEL_HEALTH_FILE",
                "/tmp/opencitadel-execution-kernel.health",
            )
        )
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def request_shutdown(self) -> None:
        self._stopping.set()

    async def run_once(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        results = await asyncio.gather(
            self._runtime.effects.run_once(now=current),
            self._runtime.timers.run_once(now=current),
            self._runtime.retention.run_once(now=current),
            return_exceptions=True,
        )
        work = 0
        for lane, result in zip(("effects", "timers", "retention"), results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "execution kernel lane %s failed",
                    lane,
                    exc_info=(type(result), result, result.__traceback__),
                )
            else:
                work += int(result)
        return work

    async def run(self) -> None:
        logger.info("execution kernel started")
        while not self._stopping.is_set():
            work = await self.run_once()
            if work == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._idle_poll_seconds)

    async def run_heartbeat(self) -> None:
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
        marker = {
            "pid": os.getpid(),
            "updated_at_epoch": time.time(),
            "kernel": "v2",
        }
        temporary = self._health_file.with_suffix(f"{self._health_file.suffix}.tmp")
        temporary.write_text(json.dumps(marker), encoding="utf-8")
        temporary.replace(self._health_file)


def install_signal_handlers(request_stop: Callable[[], None]) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_stop)


async def _wait_for_stop_or_failure(runtime: KernelRuntime) -> TaskFailure | None:
    stop_waiter = asyncio.create_task(runtime.supervisor.stop_event.wait())
    failure_waiter = asyncio.create_task(runtime.supervisor.wait_for_critical_failure())
    try:
        done, _ = await asyncio.wait(
            (stop_waiter, failure_waiter), return_when=asyncio.FIRST_COMPLETED
        )
        return failure_waiter.result() if failure_waiter in done else None
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
    with authorization_scope(AuthorizationContext.system("execution-kernel")):
        async with runtime_factory(settings) as runtime:
            process = ExecutionKernelProcess(
                runtime=runtime.kernel,
                stopping=runtime.supervisor.stop_event,
                idle_poll_seconds=settings.execution_idle_poll_seconds,
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
    from prometheus_client import make_wsgi_app

    metrics_app = make_wsgi_app()

    def app(environ, start_response):
        scheme, _, provided = environ.get("HTTP_AUTHORIZATION", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(provided, token):
            start_response("401 Unauthorized", [("Content-Type", "text/plain")])
            return [b"Unauthorized\n"]
        return metrics_app(environ, start_response)

    return app


def start_kernel_metrics_server(settings: DeploymentSettings) -> None:
    port = settings.execution_kernel_metrics_port
    if not port:
        return
    if settings.metrics_token:
        from threading import Thread
        from wsgiref.simple_server import WSGIRequestHandler, make_server

        class _QuietHandler(WSGIRequestHandler):
            def log_message(self, *args: object) -> None:
                return

        server = make_server(
            "0.0.0.0",
            port,
            _token_guarded_metrics_app(settings.metrics_token),
            handler_class=_QuietHandler,
        )
        Thread(target=server.serve_forever, daemon=True).start()
        return
    from prometheus_client import start_http_server

    start_http_server(port, addr="127.0.0.1")
    logger.warning("kernel metrics bound to loopback because METRICS_TOKEN is unset")


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
