#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.external.task import RecoverableTaskReconciliationRequired
from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.task.task_state import TaskStatus
from app.worker.main import AgentWorker


@pytest.mark.asyncio
async def test_worker_preserves_session_and_propagates_reconciliation_retry():
    session = Session(
        id="session-1",
        task_id="task-1",
        status=SessionStatus.COMPLETED,
        model_id="model-1",
    )
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.update_status = AsyncMock()

    task_state = MagicMock()
    task_state.get_task_meta = AsyncMock(
        return_value={
            "status": TaskStatus.PENDING.value,
            "run_reconciliation": {
                "run_epoch_id": "task-1:input-1",
                "outcome": {
                    "status": "succeeded",
                    "error": None,
                    "usage": {},
                },
            },
        },
    )
    task_state.clear_cancel = AsyncMock()
    task_state.is_cancelled = AsyncMock(return_value=False)
    task_state.wait_for_cancel = AsyncMock(return_value=False)

    runner = MagicMock()
    runner.cleanup = AsyncMock()
    runner_factory = MagicMock()
    runner_factory.create_runner = AsyncMock(return_value=runner)

    task = MagicMock()
    task.is_done = AsyncMock(return_value=True)
    task.execute_locally = AsyncMock()
    task.recoverable_error = RecoverableTaskReconciliationRequired(
        "retry reconciliation",
    )
    task_cls = MagicMock(return_value=task)

    container = MagicMock()
    container.mcp_connection_pool.return_value.release_stale = AsyncMock()
    container.a2a_connection_pool.return_value.release_stale = AsyncMock()

    worker = object.__new__(AgentWorker)
    worker._task_state = task_state
    worker._runner_factory = runner_factory
    worker._task_cls = task_cls

    runtime = SimpleNamespace(
        model_resilience=SimpleNamespace(
            fast_fail_on_open_circuit=False,
        ),
    )
    with patch("app.worker.main.get_uow", return_value=uow), patch(
        "app.worker.main.get_runtime_config",
        return_value=runtime,
    ), patch(
        "app.worker.main.get_worker_container",
        return_value=container,
    ):
        with pytest.raises(RecoverableTaskReconciliationRequired):
            await worker._execute_job("task-1", "session-1", 1)

    uow.session.update_status.assert_not_awaited()
    task.execute_locally.assert_awaited_once()
