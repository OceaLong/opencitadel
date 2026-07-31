#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from types import SimpleNamespace

import pytest

from app.infrastructure.external.task.task_state import (
    TaskStateService,
    TaskStatus,
)


class _MemoryRedisClient:
    def __init__(self):
        self.values = {}
        self.expiries = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value
        if "ex" in kwargs:
            self.expiries[key] = kwargs["ex"]
        return True

    async def eval(
        self,
        _script,
        _num_keys,
        key,
        run_generation,
        updates,
        removals,
        ttl,
    ):
        raw = self.values.get(key)
        if raw is None:
            return -1
        payload = json.loads(raw)
        if int(payload.get("run_generation", 1)) != int(run_generation):
            return 0
        payload.update(json.loads(updates))
        for field in json.loads(removals):
            payload.pop(field, None)
        self.values[key] = json.dumps(payload)
        self.expiries[key] = int(ttl)
        return 1


@pytest.mark.asyncio
async def test_run_reconciliation_survives_service_reconstruction_until_cleared():
    client = _MemoryRedisClient()
    first = TaskStateService.__new__(TaskStateService)
    first._redis = SimpleNamespace(client=client)
    second = TaskStateService.__new__(TaskStateService)
    second._redis = SimpleNamespace(client=client)
    task_id = "task-reconcile"
    client.values[first.meta_key(task_id)] = json.dumps(
        {
            "task_id": task_id,
            "session_id": "session-1",
            "status": "pending",
            "run_generation": 1,
            "unrelated": "preserved",
        }
    )

    await first.set_run_reconciliation(
        task_id,
        1,
        "task-reconcile:input-1",
        {
            "status": "failed",
            "error": {"message": "agent failed", "code": "AGENT_FAILED"},
            "usage": {},
        },
    )

    assert await second.get_run_reconciliation(task_id, 1) == {
        "run_generation": 1,
        "run_epoch_id": "task-reconcile:input-1",
        "outcome": {
            "status": "failed",
            "error": {"message": "agent failed", "code": "AGENT_FAILED"},
            "usage": {},
        },
    }
    assert (await second.get_task_meta(task_id))["unrelated"] == "preserved"
    assert client.expiries[first.meta_key(task_id)] == 86400 * 7

    await second.clear_run_reconciliation(task_id, 1)
    assert await first.get_run_reconciliation(task_id, 1) is None


@pytest.mark.asyncio
async def test_critical_mutations_reject_missing_task_metadata():
    client = _MemoryRedisClient()
    state = TaskStateService.__new__(TaskStateService)
    state._redis = SimpleNamespace(client=client)

    with pytest.raises(RuntimeError, match="metadata"):
        await state.set_run_reconciliation(
            "missing-task",
            1,
            "missing-task:input-1",
            {
                "status": "succeeded",
                "error": None,
                "usage": {},
            },
        )
    with pytest.raises(RuntimeError, match="metadata"):
        await state.set_status("missing-task", 1, TaskStatus.DONE)

    assert await state.get_task_meta("missing-task") is None
