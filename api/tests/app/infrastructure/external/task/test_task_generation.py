#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.domain.external.task import RecoverableTaskReconciliationRequired
from app.domain.models.run_outcome import RunOutcome
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import (
    TASK_META_TTL_SECONDS,
    TaskStateService,
    TaskStatus,
)


class _GenerationRedisClient:
    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.streams = {}
        self.acked = []
        self.deleted = []
        self.xadd_error = None
        self.before_begin_recovery = None

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        if "ex" in kwargs:
            self.expiries[key] = int(kwargs["ex"])
        return True

    async def xadd(self, stream, fields, **_kwargs):
        if self.xadd_error is not None:
            raise self.xadd_error
        entries = self.streams.setdefault(stream, [])
        message_id = f"{len(entries) + 1}-0"
        entries.append((message_id, dict(fields)))
        return message_id

    async def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream, message_id):
        self.deleted.append((stream, message_id))
        return 1

    async def eval(self, script, num_keys, *args):
        if "opencitadel:task-meta-generation-cas" in script:
            assert num_keys == 1
            key, expected_generation, updates, removals, ttl = args
            raw = self.values.get(key)
            if raw is None:
                return -1
            payload = json.loads(raw)
            if int(payload.get("run_generation", 1)) != int(expected_generation):
                return 0
            payload.update(json.loads(updates))
            for field in json.loads(removals):
                payload.pop(field, None)
            self.values[key] = json.dumps(payload)
            self.expiries[key] = int(ttl)
            return 1
        if "opencitadel:begin-recovery-generation" in script:
            assert num_keys == 1
            (
                key,
                expected_generation,
                updated_at,
                ttl,
                updates,
                removals,
                expected_identity,
                durable_message_id,
            ) = args
            if self.before_begin_recovery is not None:
                hook = self.before_begin_recovery
                self.before_begin_recovery = None
                await hook()
            raw = self.values.get(key)
            if raw is None:
                return -1
            payload = json.loads(raw)
            if int(payload.get("run_generation", 1)) != int(expected_generation):
                return 0
            identity = json.loads(expected_identity)
            if identity is not None and any(
                [
                    str(payload.get("status") or "")
                    != str(identity.get("status") or ""),
                    str(payload.get("session_id") or "")
                    != str(identity.get("session_id") or ""),
                    int(payload.get("retry_count") or 0)
                    != int(identity["retry_count"]),
                    str(payload.get("error_code") or "")
                    != str(identity.get("error_code") or ""),
                    str(payload.get("last_error") or "")
                    != str(identity.get("last_error") or ""),
                ]
            ):
                return -2
            payload.update(json.loads(updates))
            for field in json.loads(removals):
                payload.pop(field, None)
            next_generation = int(expected_generation) + 1
            payload["run_generation"] = next_generation
            payload["status"] = TaskStatus.PENDING.value
            payload["updated_at"] = float(updated_at)
            payload["last_heartbeat_at"] = None
            payload["worker_id"] = ""
            payload["durable_dispatch_generation"] = next_generation
            payload["durable_dispatch_message_id"] = durable_message_id
            reconciliation = payload.get("run_reconciliation")
            if isinstance(reconciliation, dict):
                reconciliation["run_generation"] = next_generation
            self.values[key] = json.dumps(payload)
            self.expiries[key] = int(ttl)
            return next_generation
        raise AssertionError("unexpected Lua script")


def _state(client: _GenerationRedisClient) -> TaskStateService:
    state = TaskStateService.__new__(TaskStateService)
    state._redis = SimpleNamespace(client=client)
    state._runtime_settings = SimpleNamespace(
        dispatch_maxlen=1000,
        stream_maxlen=1000,
        task_dispatch_max_retries=3,
    )
    state.ensure_consumer_group = _async_noop
    return state


async def _async_noop():
    return None


@pytest.mark.asyncio
async def test_dispatch_and_metadata_carry_run_generation():
    client = _GenerationRedisClient()
    state = _state(client)

    await state.register_task("task-1", "session-1", run_generation=3)
    await state.dispatch("task-1", "session-1", 3)

    meta = await state.get_task_meta("task-1")
    assert meta["run_generation"] == 3
    assert client.streams["task:dispatch"] == [
        (
            "1-0",
            {
                "task_id": "task-1",
                "session_id": "session-1",
                "run_generation": "3",
            },
        ),
    ]
    assert state._parse_dispatch_message(
        (
            b"9-0",
            {
                b"task_id": b"task-1",
                b"session_id": b"session-1",
                b"run_generation": b"3",
            },
        )
    ) == (b"9-0", "task-1", "session-1", 3)


@pytest.mark.asyncio
async def test_existing_untagged_metadata_and_dispatch_survive_upgrade():
    client = _GenerationRedisClient()
    state = _state(client)
    client.values["task:meta:legacy-task"] = json.dumps(
        {
            "task_id": "legacy-task",
            "session_id": "legacy-session",
            "status": TaskStatus.PENDING.value,
            "run_generation": 2,
        }
    )
    existing_dispatch = (
        b"17-0",
        {
            b"task_id": b"legacy-task",
            b"session_id": b"legacy-session",
            b"run_generation": b"2",
        },
    )

    assert await state.get_task_meta("legacy-task") == {
        "task_id": "legacy-task",
        "session_id": "legacy-session",
        "status": TaskStatus.PENDING.value,
        "run_generation": 2,
    }
    assert state._parse_dispatch_message(existing_dispatch) == (
        b"17-0",
        "legacy-task",
        "legacy-session",
        2,
    )


@pytest.mark.asyncio
async def test_stale_generation_cannot_change_status_or_heartbeat():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=3)

    assert await state.set_status("task-1", 2, TaskStatus.FAILED) is False
    assert await state.record_heartbeat("task-1", 2, "old-worker") is False

    meta = await state.get_task_meta("task-1")
    assert meta["status"] == TaskStatus.PENDING.value
    assert meta["worker_id"] == ""
    assert meta["last_heartbeat_at"] is None


@pytest.mark.asyncio
async def test_stale_generation_cannot_replace_or_clear_newer_reconciliation():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=3)
    current = {
        "status": "succeeded",
        "error": None,
        "usage": {"total_tokens": 7},
    }

    assert await state.set_run_reconciliation(
        "task-1",
        3,
        "task-1:input-new",
        current,
    ) is True
    assert await state.set_run_reconciliation(
        "task-1",
        2,
        "task-1:input-old",
        {
            "status": "failed",
            "error": {"message": "late failure", "code": "LATE"},
            "usage": {},
        },
    ) is False
    assert await state.clear_run_reconciliation("task-1", 2) is False

    assert await state.get_run_reconciliation("task-1", 3) == {
        "run_generation": 3,
        "run_epoch_id": "task-1:input-new",
        "outcome": current,
    }


@pytest.mark.asyncio
async def test_generation_cas_does_not_resurrect_missing_key_and_refreshes_ttl():
    client = _GenerationRedisClient()
    state = _state(client)

    with pytest.raises(RuntimeError, match="metadata"):
        await state.set_status("missing", 1, TaskStatus.DONE)
    assert state.meta_key("missing") not in client.values

    await state.register_task("task-1", "session-1", run_generation=1)
    client.expiries[state.meta_key("task-1")] = 10
    assert await state.set_status("task-1", 1, TaskStatus.RUNNING) is True
    assert client.expiries[state.meta_key("task-1")] == TASK_META_TTL_SECONDS


@pytest.mark.asyncio
async def test_concurrent_recovery_increments_once_and_atomically_promotes_proposal():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=4)
    assert await state.set_run_reconciliation(
        "task-1",
        4,
        "task-1:input-1",
        {
            "status": "waiting",
            "error": None,
            "usage": {},
        },
    ) is True
    replacement_message_id = await state.dispatch("task-1", "session-1", 5)

    attempts = await asyncio.gather(
        state.begin_recovery_attempt(
            "task-1",
            4,
            durable_dispatch_message_id=replacement_message_id,
        ),
        state.begin_recovery_attempt(
            "task-1",
            4,
            durable_dispatch_message_id=replacement_message_id,
        ),
    )
    assert attempts.count(5) == 1
    assert attempts.count(None) == 1

    meta = await state.get_task_meta("task-1")
    assert meta["run_generation"] == 5
    assert meta["run_reconciliation"]["run_generation"] == 5
    assert meta["status"] == TaskStatus.PENDING.value
    assert meta["durable_dispatch_generation"] == 5
    assert meta["durable_dispatch_message_id"] == replacement_message_id
    assert await state.can_ack_stale_dispatch("task-1", 4) is True
    assert client.expiries[state.meta_key("task-1")] == TASK_META_TTL_SECONDS


@pytest.mark.asyncio
async def test_stale_source_requires_current_generation_dispatch_proof():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=3)

    assert await state.can_ack_stale_dispatch("task-1", 1) is False

    payload = await state.get_task_meta("task-1")
    payload["durable_dispatch_generation"] = 2
    payload["durable_dispatch_message_id"] = "old-message"
    client.values[state.meta_key("task-1")] = json.dumps(payload)

    assert await state.can_ack_stale_dispatch("task-1", 1) is False

    payload["durable_dispatch_generation"] = 3
    payload["durable_dispatch_message_id"] = "current-message"
    client.values[state.meta_key("task-1")] = json.dumps(payload)

    assert await state.can_ack_stale_dispatch("task-1", 1) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "racing_update",
    [
        {"status": TaskStatus.RUNNING.value},
        {"status": TaskStatus.DONE.value},
        {"status": TaskStatus.CANCELLED.value},
        {
            "status": TaskStatus.FAILED.value,
            "retry_count": 4,
            "last_error": "different failure",
        },
    ],
)
async def test_dlq_identity_change_between_read_and_recovery_cas_is_rejected(
        racing_update,
):
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=3)
    meta = await state.get_task_meta("task-1")
    meta.update(
        {
            "status": TaskStatus.FAILED.value,
            "retry_count": 3,
            "error_code": "MODEL_UNAVAILABLE",
            "last_error": "boom",
        }
    )
    client.values[state.meta_key("task-1")] = json.dumps(meta)

    async def race_identity():
        raced = await state.get_task_meta("task-1")
        raced.update(racing_update)
        client.values[state.meta_key("task-1")] = json.dumps(raced)

    client.before_begin_recovery = race_identity

    replayed = await state.replay_dlq_entry(
        "dlq-source-1",
        {
            "task_id": "task-1",
            "session_id": "session-1",
            "run_generation": "3",
            "retry_count": "3",
            "error_code": "MODEL_UNAVAILABLE",
            "error": "boom",
        },
    )

    assert replayed is False
    raced_meta = await state.get_task_meta("task-1")
    assert raced_meta["run_generation"] == 3
    for key, value in racing_update.items():
        assert raced_meta[key] == value
    assert client.deleted == []


@pytest.mark.asyncio
async def test_dlq_beyond_target_remains_until_current_dispatch_is_proven():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=3)
    meta = await state.get_task_meta("task-1")
    meta.update(
        {
            "status": TaskStatus.FAILED.value,
            "retry_count": 3,
            "error_code": "MODEL_UNAVAILABLE",
            "last_error": "boom",
        }
    )
    client.values[state.meta_key("task-1")] = json.dumps(meta)
    fields = {
        "task_id": "task-1",
        "session_id": "session-1",
        "run_generation": "3",
        "retry_count": "3",
        "error_code": "MODEL_UNAVAILABLE",
        "error": "boom",
    }

    async def advance_without_dispatch():
        raced = await state.get_task_meta("task-1")
        raced.update(
            {
                "run_generation": 5,
                "status": TaskStatus.PENDING.value,
            }
        )
        client.values[state.meta_key("task-1")] = json.dumps(raced)

    client.before_begin_recovery = advance_without_dispatch

    assert await state.replay_dlq_entry("source-1", fields) is False
    assert await state.replay_dlq_entry("source-1", fields) is False
    assert client.deleted == []

    current = await state.get_task_meta("task-1")
    current["durable_dispatch_generation"] = 5
    current["durable_dispatch_message_id"] = "current-5"
    client.values[state.meta_key("task-1")] = json.dumps(current)

    assert await state.replay_dlq_entry("source-1", fields) is False
    assert client.deleted == [("task:dispatch:dlq", "source-1")]


@pytest.mark.asyncio
async def test_retry_xadd_failure_keeps_source_and_generation_reclaimable():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)
    client.xadd_error = RuntimeError("xadd unavailable")

    try:
        result = await state.mark_dispatch_failure(
            "source-1",
            "task-1",
            "session-1",
            1,
            "transient",
        )
    except RuntimeError:
        result = "raised"

    assert result is False
    assert (await state.get_task_meta("task-1"))["run_generation"] == 1
    assert client.acked == []


@pytest.mark.asyncio
async def test_retry_missing_metadata_preserves_only_source():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)
    client.values.pop(state.meta_key("task-1"))

    result = await state.mark_dispatch_failure(
        "source-missing",
        "task-1",
        "session-1",
        1,
        "transient",
    )

    assert result is False
    assert client.acked == []
    assert client.streams == {}


@pytest.mark.asyncio
async def test_retry_promotion_records_actual_xadd_before_stale_source_ack():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    assert await state.mark_dispatch_failure(
        "source-1",
        "task-1",
        "session-1",
        1,
        "transient",
    ) is True

    meta = await state.get_task_meta("task-1")
    assert meta["run_generation"] == 2
    assert meta["durable_dispatch_generation"] == 2
    assert meta["durable_dispatch_message_id"] == "1-0"

    assert await state.mark_dispatch_failure(
        "source-after-crash",
        "task-1",
        "session-1",
        1,
        "transient",
    ) is False
    assert [entry[2] for entry in client.acked] == [
        "source-1",
        "source-after-crash",
    ]


@pytest.mark.asyncio
async def test_retry_crash_during_generation_cas_keeps_source_and_replacement():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    async def crash_during_cas(_task_id, _generation, **_kwargs):
        raise RuntimeError("simulated process crash")

    state.begin_recovery_attempt = crash_during_cas

    with pytest.raises(RuntimeError, match="process crash"):
        await state.mark_dispatch_failure(
            "source-1",
            "task-1",
            "session-1",
            1,
            "transient",
        )

    assert client.streams["task:dispatch"] == [
        (
            "1-0",
            {
                "task_id": "task-1",
                "session_id": "session-1",
                "run_generation": "2",
            },
        ),
    ]
    assert (await state.get_task_meta("task-1"))["run_generation"] == 1
    assert client.acked == []


@pytest.mark.asyncio
async def test_retry_cas_loser_preserves_source_without_current_proof():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    async def lose_generation_cas(_task_id, _generation, **_kwargs):
        return None

    state.begin_recovery_attempt = lose_generation_cas

    replayed = await state.mark_dispatch_failure(
        "source-1",
        "task-1",
        "session-1",
        1,
        "transient",
    )

    assert replayed is False
    assert client.streams["task:dispatch"][0][1]["run_generation"] == "2"
    assert client.acked == []


@pytest.mark.asyncio
async def test_retry_beyond_target_without_current_dispatch_preserves_source():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    async def advance_beyond_target(task_id, _generation, **_kwargs):
        payload = json.loads(client.values[state.meta_key(task_id)])
        payload["run_generation"] = 3
        client.values[state.meta_key(task_id)] = json.dumps(payload)
        return None

    state.begin_recovery_attempt = advance_beyond_target

    result = await state.mark_dispatch_failure(
        "source-1",
        "task-1",
        "session-1",
        1,
        "transient",
    )

    assert result is False
    assert (await state.get_task_meta("task-1"))["run_generation"] == 3
    assert [
        fields["run_generation"]
        for _, fields in client.streams["task:dispatch"]
    ] == ["2"]
    assert client.acked == []


@pytest.mark.asyncio
async def test_terminal_dlq_xadd_failure_keeps_source_generation_nonterminal():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)
    client.xadd_error = RuntimeError("dlq unavailable")

    try:
        result = await state.mark_dispatch_failure(
            "source-1",
            "task-1",
            "session-1",
            1,
            "fatal",
            fast_fail=True,
        )
    except RuntimeError:
        result = "raised"

    assert result is False
    meta = await state.get_task_meta("task-1")
    assert meta["run_generation"] == 1
    assert meta["status"] == TaskStatus.PENDING.value
    assert client.acked == []


@pytest.mark.asyncio
async def test_terminal_crash_after_dlq_xadd_keeps_source_and_durable_dlq():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    async def crash_status_cas(*_args, **_kwargs):
        raise RuntimeError("simulated status CAS crash")

    state._mutate_task_meta = crash_status_cas

    with pytest.raises(RuntimeError, match="status CAS crash"):
        await state.mark_dispatch_failure(
            "source-1",
            "task-1",
            "session-1",
            1,
            "fatal",
            fast_fail=True,
        )

    assert client.streams["task:dispatch:dlq"][0][1][
        "run_generation"
    ] == "1"
    assert client.acked == []


@pytest.mark.asyncio
async def test_terminal_status_cas_loser_acks_only_after_durable_dlq():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)

    async def lose_status_cas(*_args, **_kwargs):
        return 0

    state._mutate_task_meta = lose_status_cas

    result = await state.mark_dispatch_failure(
        "source-1",
        "task-1",
        "session-1",
        1,
        "fatal",
        fast_fail=True,
    )

    assert result is False
    assert client.streams["task:dispatch:dlq"][0][1]["task_id"] == "task-1"
    assert client.acked[0][2] == "source-1"


class _SucceededRunner:
    async def invoke(self, _task):
        return RunOutcome.succeeded()

    async def on_done(self, _task):
        return None


class _CountingRunner:
    def __init__(self):
        self.invocations = 0

    async def invoke(self, _task):
        self.invocations += 1
        return RunOutcome.succeeded()

    async def on_done(self, _task):
        return None


@pytest.mark.asyncio
async def test_rejected_yielding_running_cas_never_starts_runner():
    runner = _CountingRunner()

    async def reject_after_yield(_task_id, _generation, _status):
        await asyncio.sleep(0)
        return False

    state = SimpleNamespace(set_status=reject_after_yield)
    stale_task = RedisStreamTask(
        task_id="task-stale-start",
        session_id="session-1",
        run_generation=1,
        task_runner=runner,
        task_state=state,
    )

    with pytest.raises(RecoverableTaskReconciliationRequired):
        await stale_task.execute_locally()

    assert runner.invocations == 0
    assert ("task-stale-start", 1) not in RedisStreamTask._local_executions


@pytest.mark.asyncio
async def test_old_worker_finishing_after_recovery_cannot_map_or_clear_new_generation():
    client = _GenerationRedisClient()
    state = _state(client)
    await state.register_task("task-1", "session-1", run_generation=1)
    replacement_message_id = await state.dispatch("task-1", "session-1", 2)
    assert await state.begin_recovery_attempt(
        "task-1",
        1,
        durable_dispatch_message_id=replacement_message_id,
    ) == 2
    assert await state.set_run_reconciliation(
        "task-1",
        2,
        "task-1:recovered",
        {
            "status": "waiting",
            "error": None,
            "usage": {},
        },
    ) is True
    old_task = RedisStreamTask(
        task_id="task-1",
        session_id="session-1",
        run_generation=1,
        task_runner=_SucceededRunner(),
        task_state=state,
    )

    with pytest.raises(RecoverableTaskReconciliationRequired):
        await old_task.execute_locally()

    meta = await state.get_task_meta("task-1")
    assert meta["run_generation"] == 2
    assert meta["status"] == TaskStatus.PENDING.value
    assert meta["run_reconciliation"]["run_epoch_id"] == "task-1:recovered"
