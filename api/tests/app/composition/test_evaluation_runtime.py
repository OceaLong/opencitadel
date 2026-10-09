"""Observable lane work and shutdown; business recovery uses owned PostgreSQL."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.composition.tasks import TaskKind, TaskSupervisor
from app.domain.models.scope import OwnerScope


@pytest.mark.asyncio
async def test_runtime_discovers_first_scores_and_polls_late_intents_without_runs():
    from app.application.evaluation.runtime import EvaluationRuntime

    calls = []
    scope, batch = OwnerScope.personal(str(uuid4())), uuid4()

    async def discover():
        calls.append("discover")
        return [(scope, batch)]

    async def score(owner, identity, *, limit):
        assert (owner, identity) == (scope, batch)
        calls.append("score")

    async def tick(**kwargs):
        calls.append("reconcile")

    async def schedule(now):
        assert isinstance(now, datetime)
        calls.append("schedule")

    async def cleanup():
        calls.append("cleanup")

    runtime = EvaluationRuntime(
        scheduler=SimpleNamespace(tick=schedule),
        rules=SimpleNamespace(score_batch=score),
        judge=SimpleNamespace(score_batch=score, tick=tick),
        reviews=SimpleNamespace(tick=tick),
        discover=discover,
        cleanup=(cleanup, cleanup),
    )
    await runtime.schedule()
    await runtime.score()
    await runtime.reconcile()
    await runtime.clean()
    assert calls == [
        "schedule",
        "discover",
        "score",
        "score",
        "reconcile",
        "reconcile",
        "cleanup",
        "cleanup",
    ]


@pytest.mark.asyncio
async def test_failed_lane_withdraws_supervisor_readiness_and_shutdown_is_bounded():
    from app.application.evaluation.runtime import EvaluationRuntime

    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.05)

    async def broken():
        raise RuntimeError("storage unavailable")

    await supervisor.start(
        "evaluation-scoring",
        lambda: EvaluationRuntime.run(
            broken, stop_event=supervisor.stop_event, interval_seconds=0.01
        ),
        kind=TaskKind.CRITICAL,
    )
    failure = await asyncio.wait_for(supervisor.wait_for_critical_failure(), 1)
    assert str(failure.error) == "storage unavailable"
    assert not supervisor.ready
    await supervisor.stop()
    assert not supervisor.pending_names


@pytest.mark.asyncio
async def test_shutdown_stops_polling_without_waiting_for_interval():
    from app.application.evaluation.runtime import EvaluationRuntime

    stop = asyncio.Event()
    calls = []

    async def action():
        calls.append("once")
        stop.set()

    await EvaluationRuntime.run(action, stop_event=stop, interval_seconds=100)
    assert calls == ["once"]


@pytest.mark.asyncio
async def test_scheduler_renews_claim_while_external_preparation_waits():
    from contextlib import asynccontextmanager

    from app.application.evaluation.scheduler import Scheduler

    renewed = asyncio.Event()

    @asynccontextmanager
    async def factory(*args):
        class Repo:
            async def renew(self, claim):
                assert claim == {"generation": 7}
                renewed.set()

        async def commit():
            pass

        yield SimpleNamespace(evaluation_batch=Repo(), commit=commit)

    scheduler = Scheduler(factory, None, None, execution_policy=None, preflight_factory=None)
    async with scheduler.renewing({"generation": 7}, interval_seconds=0.001):
        await asyncio.wait_for(renewed.wait(), 1)
    assert not [
        task for task in asyncio.all_tasks() if task.get_name() == "evaluation-claim-renewal"
    ]


@pytest.mark.asyncio
async def test_archive_rejects_unrecognized_resource_before_opening_database():
    from app.application.evaluation.archive_service import ArchiveService

    def forbidden(*args):
        raise AssertionError("invalid resource reached database")

    with pytest.raises(ValueError, match="archive_kind_invalid"):
        await ArchiveService(forbidden).archive(
            None,
            None,
            kind="execution-events",
            identity=uuid4(),
            expected_revision=1,
            request_id="owned",
        )


@pytest.mark.asyncio
async def test_existing_resource_pin_exception_has_typed_http_conflict():
    import httpx
    from fastapi import FastAPI

    from app.domain.models.resource_pin import ResourcePinned
    from app.interfaces.errors.exception_handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.delete("/files/owned")
    async def delete():
        raise ResourcePinned("resource is pinned", resource_kind="file", resource_id="owned")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.delete("/files/owned")
    assert result.status_code == 409
    assert result.json()["error_key"] == "resource_pinned"
    assert result.json()["data"] == {"resource_kind": "file", "resource_id": "owned"}


@pytest.mark.asyncio
async def test_scheduler_heartbeat_preserves_original_business_error():
    from app.application.evaluation.scheduler import Scheduler

    scheduler = object.__new__(Scheduler)
    with pytest.raises(ValueError, match="original_business_error"):
        async with scheduler.renewing({}, interval_seconds=60):
            raise ValueError("original_business_error")
