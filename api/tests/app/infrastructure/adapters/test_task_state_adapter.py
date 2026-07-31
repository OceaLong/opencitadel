#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, create_autospec

import pytest

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.external.task_state_port import TaskStatePort
from app.infrastructure.adapters import domain_ports
from app.infrastructure.adapters.domain_ports import RedisTaskStateAdapter
from app.infrastructure.external.task.task_state import TaskStateService


@pytest.mark.asyncio
async def test_production_adapter_registers_candidate_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_state = create_autospec(TaskStateService, instance=True)
    task_state.register_task = AsyncMock()
    monkeypatch.setattr(domain_ports, "get_task_state", lambda: task_state)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service."
        "RedisStreamTask.dispatch_to_worker",
        dispatch,
    )

    adapter = RedisTaskStateAdapter()
    assert isinstance(adapter, TaskStatePort)
    service = KnowledgeBaseService(
        uow_factory=lambda: None,  # not used by candidate dispatch
        file_storage=object(),
        task_state_port=adapter,
    )

    await service._dispatch_candidate_task("build-1", "kb-1")

    task_state.register_task.assert_awaited_once_with(
        "build-1",
        session_id="kb-ingest:kb-1",
        task_type="kb_ingest",
        resource_id="kb-1",
        request_id="",
        run_generation=1,
    )
    dispatch.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_production_adapter_exposes_legacy_service_task_state_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_state = create_autospec(TaskStateService, instance=True)
    task_state.is_done = AsyncMock(return_value=False)
    task_state.heartbeat_is_stale.return_value = True
    task_state.clear_cancel = AsyncMock()
    monkeypatch.setattr(domain_ports, "get_task_state", lambda: task_state)

    adapter = RedisTaskStateAdapter()

    assert await adapter.is_done("task-1") is False
    assert adapter.heartbeat_is_stale({"updated_at": 1.0}, 30.0) is True
    await adapter.clear_cancel("task-1")

    task_state.is_done.assert_awaited_once_with("task-1")
    task_state.heartbeat_is_stale.assert_called_once_with(
        {"updated_at": 1.0},
        30.0,
    )
    task_state.clear_cancel.assert_awaited_once_with("task-1")
