#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.llm.circuit_breaker import LLMCircuitBreaker
from app.worker.main import AgentWorker


async def _test_reconcile_skips_open_circuit():
    session = Session(id="sess-1", task_id="task-1", status=SessionStatus.RUNNING, model_id="model-1")
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.list_recoverable_running = AsyncMock(return_value=[session])

    task_state = MagicMock()
    task_state.get_runtime_snapshot = AsyncMock(
        return_value={"is_done": False, "status": "running", "meta": None},
    )
    task_state.heartbeat_is_stale = MagicMock(return_value=True)
    task_state.dispatch = AsyncMock()

    runner_factory = MagicMock()
    runner_factory._llm_model_service = MagicMock()
    runner_factory._llm_model_service.get_default_model = AsyncMock(return_value=None)

    worker = object.__new__(AgentWorker)
    worker._task_state = task_state
    worker._checkpoint_service = MagicMock()
    worker._runner_factory = runner_factory

    breaker = MagicMock()
    breaker.is_open = AsyncMock(return_value=True)

    with patch("app.worker.main.get_uow", return_value=uow), patch(
        "app.worker.main.get_task_lease_owner",
        new=AsyncMock(return_value=None),
    ), patch("app.worker.main.get_llm_circuit_breaker", return_value=breaker):
        await AgentWorker._reconcile_orphaned_tasks(worker, "test")

    task_state.dispatch.assert_not_awaited()


def test_reconcile_skips_open_circuit():
    asyncio.run(_test_reconcile_skips_open_circuit())


async def _test_reconcile_skips_real_open_circuit():
    session = Session(id="sess-1", task_id="task-1", status=SessionStatus.RUNNING, model_id="model-1")
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.list_recoverable_running = AsyncMock(return_value=[session])

    task_state = MagicMock()
    task_state.get_runtime_snapshot = AsyncMock(
        return_value={"is_done": False, "status": "running", "meta": None},
    )
    task_state.heartbeat_is_stale = MagicMock(return_value=True)
    task_state.dispatch = AsyncMock()

    runner_factory = MagicMock()
    runner_factory._llm_model_service = MagicMock()
    runner_factory._llm_model_service.get_default_model = AsyncMock(return_value=None)

    worker = object.__new__(AgentWorker)
    worker._task_state = task_state
    worker._checkpoint_service = MagicMock()
    worker._runner_factory = runner_factory

    breaker = LLMCircuitBreaker()
    redis = _FakeRedisClient()
    redis.store[breaker._open_until_key("model-1")] = str(time.time() + 60)
    breaker._redis = SimpleNamespace(client=redis)

    with patch("app.worker.main.get_uow", return_value=uow), patch(
        "app.worker.main.get_task_lease_owner",
        new=AsyncMock(return_value=None),
    ), patch("app.worker.main.get_llm_circuit_breaker", return_value=breaker), patch(
        "app.infrastructure.external.llm.circuit_breaker.get_runtime_config",
    ) as cfg:
        cfg.return_value.model_resilience.enabled = True
        await AgentWorker._reconcile_orphaned_tasks(worker, "test")

    task_state.dispatch.assert_not_awaited()


def test_reconcile_skips_real_open_circuit():
    asyncio.run(_test_reconcile_skips_real_open_circuit())


class _FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)
