from __future__ import annotations

import asyncio

import pytest

from app.composition.tasks import (
    RestartPolicy,
    TaskKind,
    TaskState,
    TaskSupervisor,
)


@pytest.mark.asyncio
async def test_critical_failure_is_reported_once_and_requests_process_stop() -> None:
    recorded = []
    supervisor = TaskSupervisor(on_critical_failure=recorded.append)

    async def crash() -> None:
        raise RuntimeError("kernel-loop-crashed")

    await supervisor.start("kernel", crash, kind=TaskKind.CRITICAL)
    failure = await asyncio.wait_for(supervisor.wait_for_critical_failure(), 1)

    assert failure.name == "kernel"
    assert isinstance(failure.error, RuntimeError)
    assert recorded == [failure]
    assert supervisor.ready is False
    assert supervisor.stop_event.is_set()

    reports = await supervisor.stop()
    assert reports["kernel"].state is TaskState.FAILED


@pytest.mark.asyncio
async def test_auxiliary_task_restarts_with_bounded_policy() -> None:
    attempts = 0
    running = asyncio.Event()

    async def flaky() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("redis-down")
        running.set()
        await asyncio.Event().wait()

    # The task ignores the stop event, so use a tiny drain window to keep the
    # three-phase stop() fast before it falls back to cancellation.
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.05)
    await supervisor.start(
        "policy-hints",
        flaky,
        kind=TaskKind.AUXILIARY,
        restart=RestartPolicy(
            initial_seconds=0,
            maximum_seconds=0,
            multiplier=2,
            jitter=0,
        ),
    )
    await asyncio.wait_for(running.wait(), 1)
    reports = await supervisor.stop()

    assert attempts == 3
    assert reports["policy-hints"].attempts == 3
    assert reports["policy-hints"].state is TaskState.CANCELLED
    assert supervisor.pending_names == ()


@pytest.mark.asyncio
async def test_request_stop_is_idempotent_and_visible_to_cooperative_tasks() -> None:
    supervisor = TaskSupervisor()
    observed = asyncio.Event()

    async def cooperative() -> None:
        await supervisor.stop_event.wait()
        observed.set()

    await supervisor.start("cooperative", cooperative, kind=TaskKind.CRITICAL)
    supervisor.request_stop()
    supervisor.request_stop()
    await asyncio.wait_for(observed.wait(), 1)

    reports = await supervisor.stop()
    assert reports["cooperative"].state is TaskState.COMPLETED


@pytest.mark.asyncio
async def test_stop_drains_registered_and_transient_tasks() -> None:
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.05)
    cancelled: list[str] = []

    async def waits_forever(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(name)

    await supervisor.start(
        "owned",
        lambda: waits_forever("owned"),
        kind=TaskKind.CRITICAL,
    )
    await supervisor.start_transient(
        "transient",
        lambda: waits_forever("transient"),
    )

    reports = await supervisor.stop()

    assert set(cancelled) == {"owned", "transient"}
    assert reports["owned"].state is TaskState.CANCELLED
    assert reports["transient"].state is TaskState.CANCELLED
    assert supervisor.pending_names == ()
    assert await supervisor.stop() == reports


@pytest.mark.asyncio
async def test_stop_drains_in_flight_handler_to_completion_without_cancelling() -> None:
    """K2-3 排水: an in-flight handler that finishes within the shutdown window
    completes naturally — stop() must not cancel it (which would abort an
    in-flight model call and spuriously fail its Run)."""
    supervisor = TaskSupervisor(shutdown_timeout_seconds=5.0)
    events: list[str] = []
    started = asyncio.Event()

    async def slow_handler_then_drain() -> None:
        # Simulates one worker loop: an in-flight handler (sleep) that must be
        # allowed to finish, then the loop observes the stop event and exits.
        try:
            started.set()
            await asyncio.sleep(0.1)
            events.append("handler-finished")
            await supervisor.stop_event.wait()
            events.append("drained")
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    await supervisor.start("worker", slow_handler_then_drain, kind=TaskKind.CRITICAL)
    await started.wait()

    reports = await supervisor.stop()

    assert events == ["handler-finished", "drained"]
    assert reports["worker"].state is TaskState.COMPLETED


@pytest.mark.asyncio
async def test_stop_reports_task_that_exceeds_shutdown_timeout() -> None:
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.01)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def ignores_first_cancellation() -> None:
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        finally:
            finished.set()

    await supervisor.start("stuck", ignores_first_cancellation, kind=TaskKind.CRITICAL)
    reports = await supervisor.stop()

    assert reports["stuck"].state is TaskState.TIMED_OUT
    assert reports["stuck"].error is not None
    release.set()
    await asyncio.wait_for(finished.wait(), 1)
    assert supervisor.pending_names == ()


@pytest.mark.asyncio
async def test_duplicate_and_blank_names_are_rejected() -> None:
    supervisor = TaskSupervisor(shutdown_timeout_seconds=0.05)

    async def waits() -> None:
        await asyncio.Event().wait()

    with pytest.raises(ValueError, match="blank"):
        await supervisor.start(" ", waits, kind=TaskKind.CRITICAL)

    await supervisor.start("unique", waits, kind=TaskKind.CRITICAL)
    with pytest.raises(ValueError, match="already registered"):
        await supervisor.start("unique", waits, kind=TaskKind.CRITICAL)
    await supervisor.stop()


@pytest.mark.asyncio
async def test_completed_transient_task_releases_its_supervisor_name() -> None:
    supervisor = TaskSupervisor()
    completions = 0

    async def completes() -> None:
        nonlocal completions
        completions += 1

    await supervisor.start_transient("refresh", completes)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    await supervisor.start_transient("refresh", completes)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert completions == 2
    assert supervisor.pending_names == ()
    await supervisor.stop()
