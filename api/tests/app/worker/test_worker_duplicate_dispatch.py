#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.external.task import RecoverableTaskReconciliationRequired
from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.task.task_state import TaskStatus
from app.infrastructure.external.task import task_lease
from app.worker.main import AgentWorker


def _task_state(*, generation: int, status: TaskStatus = TaskStatus.PENDING):
    state = SimpleNamespace()
    state.get_task_meta = AsyncMock(
        return_value={
            "task_id": "task-1",
            "session_id": "session-1",
            "run_generation": generation,
            "status": status.value,
            "request_id": "",
        },
    )
    state.ack_dispatch = AsyncMock()
    state.can_ack_stale_dispatch = AsyncMock(return_value=True)
    state.record_heartbeat = AsyncMock(return_value=True)
    state.mark_dispatch_failure = AsyncMock()
    return state


def _worker(state, execute=None):
    worker = object.__new__(AgentWorker)
    worker._task_state = state
    worker._task_cls = object
    worker._checkpoint_service = object()
    worker._execute_job_with_lease_renewal = execute or AsyncMock()
    return worker


def _worker_patches(*, acquire, owner=None, release=None):
    return (
        patch(
            "app.worker.main.get_admission_runtime_settings",
            return_value=SimpleNamespace(task_execution_lease_seconds=60),
        ),
        patch("app.worker.main.get_worker_id", return_value="worker-current"),
        patch("app.worker.main.try_acquire_task_lease", side_effect=acquire),
        patch("app.worker.main.get_task_lease_owner", return_value=owner),
        patch(
            "app.worker.main.release_task_lease",
            side_effect=release or (lambda *_args: None),
        ),
    )


def _lease_result(name: str):
    result_type = getattr(task_lease, "TaskLeaseAcquireResult", None)
    return getattr(result_type, name) if result_type is not None else name


@pytest.mark.asyncio
async def test_stale_generation_dispatch_is_acked_without_execution_or_lease():
    state = _task_state(generation=3)
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2] as acquire, patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-old",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "ACK_DUPLICATE"
    state.ack_dispatch.assert_awaited_once_with("msg-old")
    acquire.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_generation_dispatch_is_requeued_without_acknowledgement():
    state = _task_state(generation=2)
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2] as acquire, patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-future",
            "task-1",
            "session-1",
            3,
        )

    assert decision.value == "REQUEUE"
    state.ack_dispatch.assert_not_awaited()
    acquire.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_dispatch_without_current_durable_proof_is_requeued():
    state = _task_state(generation=3)
    state.can_ack_stale_dispatch.return_value = False
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2] as acquire, patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-old",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "REQUEUE"
    state.ack_dispatch.assert_not_awaited()
    acquire.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_cas_loss_without_current_dispatch_proof_is_requeued():
    state = _task_state(generation=2)
    state.record_heartbeat.return_value = False
    state.can_ack_stale_dispatch.return_value = False
    state.get_task_meta.side_effect = [
        state.get_task_meta.return_value,
        {
            **state.get_task_meta.return_value,
            "run_generation": 3,
        },
    ]
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-raced",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "REQUEUE"
    state.can_ack_stale_dispatch.assert_awaited_once_with("task-1", 2)
    state.ack_dispatch.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_conflict_duplicate_is_acked():
    state = _task_state(generation=2)
    execute = AsyncMock()
    worker = _worker(state, execute)
    acquire = AsyncMock(
        return_value=_lease_result("SAME_GENERATION_CONFLICT"),
    )
    patches = _worker_patches(acquire=acquire, owner="worker-a")

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-1",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "ACK_DUPLICATE"
    state.ack_dispatch.assert_awaited_once_with("msg-1")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_generation_without_observed_lease_remains_unacked():
    state = _task_state(generation=2)
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ERROR")),
        owner=None,
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-race",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "REQUEUE"
    state.ack_dispatch.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_claimed_messages_execute_once_and_ack_live_duplicate():
    state = _task_state(generation=2)
    execution_started = asyncio.Event()
    finish_execution = asyncio.Event()
    lease_held = False

    async def acquire(_task_id, generation, _ttl):
        nonlocal lease_held
        assert generation == 2
        if lease_held:
            return _lease_result("SAME_GENERATION_CONFLICT")
        lease_held = True
        return _lease_result("ACQUIRED")

    async def release(_task_id, generation):
        nonlocal lease_held
        assert generation == 2
        lease_held = False

    async def execute(_task_id, _session_id, generation, _ttl):
        assert generation == 2
        execution_started.set()
        await finish_execution.wait()

    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=acquire,
        owner="worker-current",
        release=release,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        first = asyncio.create_task(
            worker._handle_claimed_job(
                "msg-1",
                "task-1",
                "session-1",
                2,
            )
        )
        await execution_started.wait()
        second_decision = await worker._handle_claimed_job(
            "msg-2",
            "task-1",
            "session-1",
            2,
        )
        finish_execution.set()
        first_decision = await first

    assert second_decision.value == "ACK_DUPLICATE"
    assert first_decision.value == "EXECUTE"
    assert [call.args[0] for call in state.ack_dispatch.await_args_list] == [
        "msg-2",
        "msg-1",
    ]


@pytest.mark.asyncio
async def test_matching_generation_reconciliation_retry_remains_unacked():
    state = _task_state(generation=5)
    execute = AsyncMock(
        side_effect=RecoverableTaskReconciliationRequired("retry proposal"),
    )
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-recoverable",
            "task-1",
            "session-1",
            5,
        )

    assert decision.value == "REQUEUE"
    state.ack_dispatch.assert_not_awaited()
    state.mark_dispatch_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_current_generation_message_is_acked_as_duplicate():
    state = _task_state(generation=2, status=TaskStatus.DONE)
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
    )

    with patches[0], patches[1], patches[2] as acquire, patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-done",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "ACK_DUPLICATE"
    state.ack_dispatch.assert_awaited_once_with("msg-done")
    acquire.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphan_recovery_creates_one_new_generation_and_dispatch():
    session = Session(
        id="session-1",
        task_id="task-1",
        status=SessionStatus.RUNNING,
        model_id="model-1",
    )
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    uow.__aexit__.return_value = None
    uow.session.list_recoverable_running.return_value = [session]

    state = SimpleNamespace()
    state.get_runtime_snapshot = AsyncMock(
        return_value={
            "is_done": False,
            "run_generation": 4,
            "meta": {"run_generation": 4, "updated_at": 0},
        },
    )
    state.heartbeat_is_stale = MagicMock(side_effect=[True, False])
    state.clear_cancel = AsyncMock()
    state.begin_recovery_attempt = AsyncMock(return_value=5)
    state.dispatch = AsyncMock(return_value="replacement-1")

    worker = object.__new__(AgentWorker)
    worker._task_state = state
    worker._checkpoint_service = SimpleNamespace(
        resume_latest_checkpoint=AsyncMock(
            return_value=SimpleNamespace(id="checkpoint-1")
        )
    )
    worker._task_cls = SimpleNamespace(
        from_task_id=MagicMock(return_value=object())
    )
    worker._runner_factory = SimpleNamespace()
    breaker = SimpleNamespace(is_open=AsyncMock(return_value=False))

    with patch("app.worker.main.get_uow", return_value=uow), patch(
        "app.worker.main.get_task_lease_owner",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.worker.main.get_llm_circuit_breaker",
        return_value=breaker,
    ), patch(
        "app.worker.main.requeue_latest_user_message",
        new=AsyncMock(return_value=True),
    ):
        await worker._reconcile_orphaned_tasks("first")
        await worker._reconcile_orphaned_tasks("second")

    state.begin_recovery_attempt.assert_awaited_once_with(
        "task-1",
        4,
        durable_dispatch_message_id="replacement-1",
    )
    state.dispatch.assert_awaited_once_with("task-1", "session-1", 5)


@pytest.mark.asyncio
async def test_terminal_transition_during_atomic_acquire_is_acked_without_execution():
    state = _task_state(generation=2)
    execute = AsyncMock()
    worker = _worker(state, execute)

    async def classify_terminal(_task_id, _generation, _ttl):
        state.get_task_meta.return_value["status"] = TaskStatus.DONE.value
        return _lease_result("TERMINAL")

    patches = _worker_patches(acquire=classify_terminal)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-terminal-race",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "ACK_DUPLICATE"
    state.ack_dispatch.assert_awaited_once_with("msg-terminal-race")
    state.record_heartbeat.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_generation_replaces_prior_generation_lease_and_executes():
    state = _task_state(generation=2)
    execute = AsyncMock()
    worker = _worker(state, execute)
    patches = _worker_patches(
        acquire=AsyncMock(return_value=_lease_result("ACQUIRED")),
        owner="crashed-generation-1-worker",
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        decision = await worker._handle_claimed_job(
            "msg-current",
            "task-1",
            "session-1",
            2,
        )

    assert decision.value == "EXECUTE"
    state.ack_dispatch.assert_awaited_once_with("msg-current")
    execute.assert_awaited_once()
