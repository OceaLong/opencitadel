#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.error_codes import MODEL_UNAVAILABLE
from app.infrastructure.external.task.task_state import TaskStateService


@pytest.mark.asyncio
async def test_replay_dlq_entry_only_model_errors():
    service = TaskStateService()
    service._redis = MagicMock()
    service._redis.client = MagicMock()
    service._redis.client.set = AsyncMock()
    service._redis.client.xdel = AsyncMock()
    service.dispatch = AsyncMock()
    service.get_task_meta = AsyncMock(return_value={"task_id": "t1", "session_id": "s1", "retry_count": 3})

    ok = await service.replay_dlq_entry(
        "1-0",
        {"task_id": "t1", "session_id": "s1", "error_code": MODEL_UNAVAILABLE, "error": "boom"},
    )
    assert ok is True
    service.dispatch.assert_awaited_once_with("t1", "s1")

    service.dispatch.reset_mock()
    skipped = await service.replay_dlq_entry(
        "2-0",
        {"task_id": "t2", "session_id": "s2", "error_code": "TASK_INFRA_FAILED"},
    )
    assert skipped is False
    service.dispatch.assert_not_awaited()
