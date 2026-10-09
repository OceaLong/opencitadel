"""The actual comparison worker is owned and stopped by the kernel supervisor."""

import asyncio
from functools import partial
from types import SimpleNamespace

import pytest

from app.composition.execution_comparison import build_comparison_diff_worker
from app.composition.tasks import TaskKind, TaskSupervisor
from app.infrastructure.external.scheduler.job_scheduler import run_maintenance_loop


@pytest.mark.asyncio
async def test_composed_worker_polls_and_supervisor_shutdown_stops_it():
    worker = build_comparison_diff_worker(
        settings=SimpleNamespace(database_authorization_signing_secret="test-comparison-secret"),
        resources=SimpleNamespace(postgres=SimpleNamespace(session_factory=None)),
        shared=SimpleNamespace(object_storage=object()),
    )
    called = asyncio.Event()
    calls = []

    async def claim():
        calls.append("claim")
        called.set()

    worker.jobs = SimpleNamespace(claim=claim)
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.1)
    await supervisor.start(
        "comparison-artifact-diff",
        partial(
            run_maintenance_loop,
            worker.process_pending,
            stop_event=supervisor.stop_event,
            interval_seconds=100,
        ),
        kind=TaskKind.CRITICAL,
    )
    await asyncio.wait_for(called.wait(), 1)
    await supervisor.stop()
    assert calls == ["claim"]
    assert not supervisor.pending_names
