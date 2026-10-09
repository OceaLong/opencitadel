import asyncio
from functools import partial
from types import SimpleNamespace

import pytest

from app.composition.execution_export import build_export_worker
from app.composition.tasks import TaskKind, TaskSupervisor
from app.infrastructure.external.scheduler.job_scheduler import run_maintenance_loop


@pytest.mark.asyncio
async def test_export_worker_lifecycle_is_owned_by_real_supervisor():
    worker, _cleanup = build_export_worker(
        settings=SimpleNamespace(database_authorization_signing_secret="secret"),
        resources=SimpleNamespace(postgres=SimpleNamespace(session_factory=None)),
        shared=SimpleNamespace(object_storage=object()),
    )
    entered = asyncio.Event()

    async def claim():
        entered.set()

    worker.repository.claim = claim
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.1)
    await supervisor.start(
        "execution-export",
        partial(
            run_maintenance_loop,
            worker.process_pending,
            stop_event=supervisor.stop_event,
            interval_seconds=100,
        ),
        kind=TaskKind.CRITICAL,
    )
    await asyncio.wait_for(entered.wait(), 1)
    assert "execution-export" in supervisor.pending_names
    await supervisor.stop()
    assert not supervisor.pending_names
