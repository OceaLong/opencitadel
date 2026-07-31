#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.error_codes import MODEL_UNAVAILABLE
from app.infrastructure.external.task.task_state import (
    TaskStateService,
    TaskStatus,
)


def _service(meta):
    service = TaskStateService.__new__(TaskStateService)
    client = MagicMock()
    client.xdel = AsyncMock()
    service._redis = SimpleNamespace(client=client)
    service._runtime_settings = SimpleNamespace(dispatch_maxlen=1000)
    service.get_task_meta = AsyncMock(return_value=meta)
    service._mutate_task_meta = AsyncMock(return_value=1)
    service.begin_recovery_attempt = AsyncMock(return_value=4)
    service.can_ack_stale_dispatch = AsyncMock(return_value=False)
    service.dispatch = AsyncMock(return_value="replacement-1")
    return service, client


def _failed_meta(*, generation=3, retry_count=3, error="boom"):
    return {
        "task_id": "t1",
        "session_id": "s1",
        "status": TaskStatus.FAILED.value,
        "retry_count": retry_count,
        "run_generation": generation,
        "error_code": MODEL_UNAVAILABLE,
        "last_error": error,
    }


def _dlq_fields(*, generation="3", retry_count="3", error="boom"):
    return {
        "task_id": "t1",
        "session_id": "s1",
        "run_generation": generation,
        "retry_count": retry_count,
        "error_code": MODEL_UNAVAILABLE,
        "error": error,
    }


@pytest.mark.asyncio
async def test_replay_matching_failed_generation_dispatches_replacement():
    service, client = _service(_failed_meta())

    ok = await service.replay_dlq_entry("1-0", _dlq_fields())

    assert ok is True
    service.dispatch.assert_awaited_once_with("t1", "s1", 4)
    service.begin_recovery_attempt.assert_awaited_once_with(
        "t1",
        3,
        durable_dispatch_message_id="replacement-1",
        expected_failed_identity={
            "status": TaskStatus.FAILED.value,
            "session_id": "s1",
            "retry_count": 3,
            "error_code": MODEL_UNAVAILABLE,
            "last_error": "boom",
        },
        updates={"retry_count": 0},
        removals=("last_error", "error_code"),
    )
    client.xdel.assert_awaited_once_with("task:dispatch:dlq", "1-0")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        _failed_meta() | {"status": TaskStatus.RUNNING.value},
        _failed_meta() | {"status": TaskStatus.DONE.value},
        _failed_meta() | {"status": TaskStatus.CANCELLED.value},
    ],
)
async def test_stale_dlq_row_is_deleted_without_mutating_current_task(meta):
    service, client = _service(meta)

    replayed = await service.replay_dlq_entry("stale-1", _dlq_fields())

    assert replayed is False
    client.xdel.assert_awaited_once_with("task:dispatch:dlq", "stale-1")
    service._mutate_task_meta.assert_not_awaited()
    service.begin_recovery_attempt.assert_not_awaited()
    service.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_generation_dlq_row_requires_current_dispatch_proof():
    service, client = _service(_failed_meta(generation=4))

    replayed = await service.replay_dlq_entry("stale-1", _dlq_fields())

    assert replayed is False
    client.xdel.assert_not_awaited()
    service.can_ack_stale_dispatch.assert_awaited_once_with("t1", 3)
    service.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_metadata_preserves_dlq_source():
    service, client = _service(None)

    replayed = await service.replay_dlq_entry("missing-1", _dlq_fields())

    assert replayed is False
    client.xdel.assert_not_awaited()
    service.dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        _dlq_fields(generation="not-an-integer"),
        _dlq_fields(retry_count="2"),
        _dlq_fields(error="different failure"),
    ],
)
async def test_malformed_or_wrong_failure_identity_is_deleted_without_replay(fields):
    service, client = _service(_failed_meta())

    replayed = await service.replay_dlq_entry("invalid-1", fields)

    assert replayed is False
    client.xdel.assert_awaited_once_with("task:dispatch:dlq", "invalid-1")
    service._mutate_task_meta.assert_not_awaited()
    service.begin_recovery_attempt.assert_not_awaited()
    service.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_model_dlq_row_remains_for_operator_handling():
    service, client = _service(_failed_meta())
    fields = _dlq_fields()
    fields["error_code"] = "TASK_INFRA_FAILED"

    replayed = await service.replay_dlq_entry("operator-1", fields)

    assert replayed is False
    client.xdel.assert_not_awaited()
    service.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_replacement_xadd_failure_keeps_failed_generation_and_source():
    service, client = _service(_failed_meta())
    service.dispatch.side_effect = RuntimeError("xadd unavailable")

    replayed = await service.replay_dlq_entry("source-1", _dlq_fields())

    assert replayed is False
    service.begin_recovery_attempt.assert_not_awaited()
    client.xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_crash_during_generation_cas_keeps_source_and_replacement():
    service, client = _service(_failed_meta())
    service.begin_recovery_attempt.side_effect = RuntimeError(
        "simulated process crash"
    )

    with pytest.raises(RuntimeError, match="process crash"):
        await service.replay_dlq_entry("source-1", _dlq_fields())

    service.dispatch.assert_awaited_once_with("t1", "s1", 4)
    client.xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_cas_loser_preserves_source_without_current_durable_proof():
    service, client = _service(_failed_meta())
    service.begin_recovery_attempt.return_value = None

    replayed = await service.replay_dlq_entry("source-1", _dlq_fields())

    assert replayed is False
    service.dispatch.assert_awaited_once_with("t1", "s1", 4)
    client.xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_cas_loser_deletes_source_with_current_durable_proof():
    service, client = _service(_failed_meta())
    service.begin_recovery_attempt.return_value = None
    service.can_ack_stale_dispatch.return_value = True

    replayed = await service.replay_dlq_entry("source-1", _dlq_fields())

    assert replayed is False
    client.xdel.assert_awaited_once_with("task:dispatch:dlq", "source-1")
