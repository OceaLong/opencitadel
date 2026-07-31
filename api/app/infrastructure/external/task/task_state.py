#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis-backed distributed task metadata, dispatch queue, and cancel control."""
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from app.infrastructure.external.runtime_settings import TaskQueueRuntimeSettings
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

TASK_META_PREFIX = "task:meta:"
TASK_CANCEL_PREFIX = "task:cancel:"
TASK_DISPATCH_STREAM = "task:dispatch"
TASK_DISPATCH_DLQ_STREAM = "task:dispatch:dlq"
WORKER_CONSUMER_GROUP = "opencitadel-workers"
TASK_META_TTL_SECONDS = 86400 * 7
OUTPUT_SEQ_INDEX_PREFIX = "task:output:seq:"
CANCEL_NOTIFY_CHANNEL_PREFIX = "task:cancel:notify:"

_GENERATION_CAS_TASK_META_MUTATION = """
-- opencitadel:task-meta-generation-cas
local raw = redis.call("GET", KEYS[1])
if not raw then
    return -1
end

local meta = cjson.decode(raw)
local current_generation = tonumber(meta["run_generation"] or 1)
local expected_generation = tonumber(ARGV[1])
if current_generation ~= expected_generation then
    return 0
end

local updates = cjson.decode(ARGV[2])
local removals = cjson.decode(ARGV[3])

for field, value in pairs(updates) do
    meta[field] = value
end
for _, field in ipairs(removals) do
    meta[field] = nil
end

redis.call("SET", KEYS[1], cjson.encode(meta), "EX", ARGV[4])
return 1
"""

_BEGIN_RECOVERY_GENERATION = """
-- opencitadel:begin-recovery-generation
local raw = redis.call("GET", KEYS[1])
if not raw then
    return -1
end

local meta = cjson.decode(raw)
local current_generation = tonumber(meta["run_generation"] or 1)
local expected_generation = tonumber(ARGV[1])
if current_generation ~= expected_generation then
    return 0
end

local expected_identity = cjson.decode(ARGV[6])
if type(expected_identity) == "table" then
    if tostring(meta["status"] or "") ~= tostring(expected_identity["status"] or "")
        or tostring(meta["session_id"] or "") ~= tostring(expected_identity["session_id"] or "")
        or tonumber(meta["retry_count"] or 0) ~= tonumber(expected_identity["retry_count"])
        or tostring(meta["error_code"] or "") ~= tostring(expected_identity["error_code"] or "")
        or tostring(meta["last_error"] or "") ~= tostring(expected_identity["last_error"] or "") then
        return -2
    end
end

local updates = cjson.decode(ARGV[4])
local removals = cjson.decode(ARGV[5])
for field, value in pairs(updates) do
    meta[field] = value
end
for _, field in ipairs(removals) do
    meta[field] = nil
end

local next_generation = current_generation + 1
meta["run_generation"] = next_generation
meta["status"] = "pending"
meta["updated_at"] = tonumber(ARGV[2])
meta["last_heartbeat_at"] = cjson.null
meta["worker_id"] = ""
meta["durable_dispatch_generation"] = next_generation
meta["durable_dispatch_message_id"] = ARGV[7]
if type(meta["run_reconciliation"]) == "table" then
    meta["run_reconciliation"]["run_generation"] = next_generation
end

redis.call("SET", KEYS[1], cjson.encode(meta), "EX", ARGV[3])
return next_generation
"""


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


_TERMINAL_STATUSES = {
    TaskStatus.DONE.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.FAILED.value,
}
_task_queue_runtime_settings = TaskQueueRuntimeSettings()


def configure_task_state_runtime(settings: TaskQueueRuntimeSettings) -> None:
    global _task_queue_runtime_settings
    _task_queue_runtime_settings = settings
    if _task_state is not None:
        _task_state.update_runtime_settings(settings)


class TaskStateService:
    """Manage task lifecycle metadata on the supported non-cluster Redis runtime."""

    def __init__(
            self,
            runtime_settings: Optional[TaskQueueRuntimeSettings] = None,
    ) -> None:
        self._redis = get_redis()
        self._runtime_settings = runtime_settings or _task_queue_runtime_settings

    def update_runtime_settings(self, settings: TaskQueueRuntimeSettings) -> None:
        self._runtime_settings = settings

    @staticmethod
    def meta_key(task_id: str) -> str:
        # This service targets the established single-node Redis key layout.
        return f"{TASK_META_PREFIX}{task_id}"

    @staticmethod
    def cancel_key(task_id: str) -> str:
        return f"{TASK_CANCEL_PREFIX}{task_id}"

    async def ensure_consumer_group(self) -> None:
        try:
            await self._redis.client.xgroup_create(
                TASK_DISPATCH_STREAM,
                WORKER_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def register_task(
            self,
            task_id: str,
            session_id: str,
            task_type: str = "agent",
            resource_id: str = "",
            request_id: str = "",
            run_generation: int = 1,
    ) -> None:
        if run_generation < 1:
            raise ValueError("run_generation must be positive")
        payload = {
            "task_id": task_id,
            "session_id": session_id,
            "task_type": task_type,
            "resource_id": resource_id,
            "request_id": request_id or "",
            "status": TaskStatus.PENDING.value,
            "run_generation": run_generation,
            "retry_count": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
            "last_heartbeat_at": None,
            "worker_id": "",
        }
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(payload),
            ex=TASK_META_TTL_SECONDS,
        )

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._redis.client.get(self.meta_key(task_id))
        if not raw:
            return None
        return json.loads(raw)

    async def _mutate_task_meta(
            self,
            task_id: str,
            run_generation: int,
            *,
            updates: Optional[Dict[str, Any]] = None,
            removals: Tuple[str, ...] = (),
    ) -> int:
        """Generation-CAS selected JSON fields and refresh task retention."""
        changed = await self._redis.client.eval(
            _GENERATION_CAS_TASK_META_MUTATION,
            1,
            self.meta_key(task_id),
            run_generation,
            json.dumps(updates or {}),
            json.dumps(removals),
            TASK_META_TTL_SECONDS,
        )
        return int(changed)

    async def set_status(
            self,
            task_id: str,
            run_generation: int,
            status: TaskStatus,
    ) -> bool:
        changed = await self._mutate_task_meta(
            task_id,
            run_generation,
            updates={
                "status": status.value,
                "updated_at": time.time(),
            },
        )
        if changed < 0:
            raise RuntimeError(
                f"Task metadata unavailable for status mutation: {task_id}"
            )
        return changed > 0

    async def get_status(self, task_id: str) -> Optional[TaskStatus]:
        meta = await self.get_task_meta(task_id)
        if not meta or not meta.get("status"):
            return None
        return TaskStatus(meta["status"])

    async def set_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
            run_epoch_id: str,
            outcome: Dict[str, Any],
    ) -> bool:
        changed = await self._mutate_task_meta(
            task_id,
            run_generation,
            updates={
                "run_reconciliation": {
                    "run_generation": run_generation,
                    "run_epoch_id": run_epoch_id,
                    "outcome": outcome,
                },
                "updated_at": time.time(),
            },
        )
        if changed < 0:
            raise RuntimeError(
                f"Task metadata unavailable for reconciliation: {task_id}"
            )
        return changed > 0

    async def get_run_reconciliation(
            self,
            task_id: str,
            run_generation: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return None
        reconciliation = meta.get("run_reconciliation")
        if not isinstance(reconciliation, dict):
            return None
        if (
            run_generation is not None
            and int(reconciliation.get("run_generation", 1)) != run_generation
        ):
            return None
        return reconciliation

    async def clear_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
    ) -> bool:
        changed = await self._mutate_task_meta(
            task_id,
            run_generation,
            updates={"updated_at": time.time()},
            removals=("run_reconciliation",),
        )
        return changed > 0

    async def begin_recovery_attempt(
            self,
            task_id: str,
            expected_generation: int,
            *,
            durable_dispatch_message_id: str,
            expected_failed_identity: Optional[Dict[str, Any]] = None,
            updates: Optional[Dict[str, Any]] = None,
            removals: Tuple[str, ...] = (),
    ) -> Optional[int]:
        """Promote a durable replacement and atomically record its proof."""
        if not durable_dispatch_message_id:
            raise ValueError("durable_dispatch_message_id is required")
        result = int(
            await self._redis.client.eval(
                _BEGIN_RECOVERY_GENERATION,
                1,
                self.meta_key(task_id),
                expected_generation,
                time.time(),
                TASK_META_TTL_SECONDS,
                json.dumps(updates or {}),
                json.dumps(removals),
                json.dumps(expected_failed_identity),
                durable_dispatch_message_id,
            )
        )
        if result == -1:
            raise RuntimeError(
                f"Task metadata unavailable for recovery mutation: {task_id}"
            )
        return result if result > 0 else None

    async def can_ack_stale_dispatch(
            self,
            task_id: str,
            source_generation: int,
    ) -> bool:
        """Prove that a stale source is superseded or no longer executable."""
        meta = await self.get_task_meta(task_id)
        if not meta:
            return False
        current_generation = int(meta.get("run_generation", 1))
        if current_generation <= source_generation:
            return False
        if meta.get("status") in _TERMINAL_STATUSES:
            return True
        try:
            durable_generation = int(meta.get("durable_dispatch_generation"))
        except (TypeError, ValueError):
            return False
        return (
            durable_generation == current_generation
            and bool(meta.get("durable_dispatch_message_id"))
        )

    async def is_done(self, task_id: str) -> bool:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return True
        return meta.get("status") in _TERMINAL_STATUSES

    @staticmethod
    def cancel_notify_channel(task_id: str) -> str:
        return f"{CANCEL_NOTIFY_CHANNEL_PREFIX}{task_id}"

    async def get_runtime_snapshot(self, task_id: str) -> Dict[str, Any]:
        """Fetch cancel flag and task meta in a single Redis pipeline round-trip."""
        pipe = self._redis.client.pipeline()
        pipe.get(self.cancel_key(task_id))
        pipe.get(self.meta_key(task_id))
        cancel_raw, meta_raw = await pipe.execute()

        cancelled = bool(cancel_raw)
        meta = json.loads(meta_raw) if meta_raw else None
        status: Optional[TaskStatus] = None
        is_done = True
        if meta:
            status_value = meta.get("status")
            if status_value:
                status = TaskStatus(status_value)
            is_done = status_value in _TERMINAL_STATUSES

        return {
            "cancelled": cancelled,
            "status": status,
            "is_done": is_done,
            "meta": meta,
            "last_heartbeat_at": meta.get("last_heartbeat_at") if meta else None,
            "worker_id": meta.get("worker_id") if meta else "",
            "run_generation": int(meta.get("run_generation", 1)) if meta else None,
        }

    async def record_heartbeat(
            self,
            task_id: str,
            run_generation: int,
            worker_id: str,
    ) -> bool:
        now = time.time()
        changed = await self._mutate_task_meta(
            task_id,
            run_generation,
            updates={
                "last_heartbeat_at": now,
                "worker_id": worker_id,
                "updated_at": now,
            },
        )
        return changed > 0

    @staticmethod
    def heartbeat_is_stale(meta: Optional[Dict[str, Any]], stale_after_seconds: float) -> bool:
        if not meta:
            return True
        heartbeat = meta.get("last_heartbeat_at") or meta.get("updated_at")
        if heartbeat is None:
            return True
        try:
            return time.time() - float(heartbeat) >= stale_after_seconds
        except (TypeError, ValueError):
            return True

    async def request_cancel(self, task_id: str) -> None:
        await self._redis.client.set(self.cancel_key(task_id), "1", ex=3600)
        meta = await self.get_task_meta(task_id)
        if meta:
            await self.set_status(
                task_id,
                int(meta.get("run_generation", 1)),
                TaskStatus.CANCELLED,
            )
        await self._redis.client.publish(self.cancel_notify_channel(task_id), "1")

    async def is_cancelled(self, task_id: str) -> bool:
        return bool(await self._redis.client.get(self.cancel_key(task_id)))

    async def wait_for_cancel(self, task_id: str, timeout_seconds: float = 30.0) -> bool:
        """Block until cancel is requested or timeout elapses."""
        if await self.is_cancelled(task_id):
            return True

        pubsub = None
        channel = self.cancel_notify_channel(task_id)
        try:
            pubsub = self._redis.client.pubsub()
            await pubsub.subscribe(channel)
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout_seconds,
            )
            if message and message.get("type") == "message":
                return True
            return await self.is_cancelled(task_id)
        except Exception as exc:
            logger.debug("等待任务取消通知失败 task_id=%s: %s", task_id, exc)
            return await self.is_cancelled(task_id)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass

    @staticmethod
    def output_seq_index_key(task_id: str) -> str:
        return f"{OUTPUT_SEQ_INDEX_PREFIX}{task_id}"

    async def set_output_seq_cursor(self, task_id: str, seq: int, stream_id: str) -> None:
        key = self.output_seq_index_key(task_id)
        await self._redis.client.hset(key, str(seq), stream_id)
        await self._redis.client.expire(key, TASK_META_TTL_SECONDS)

    async def get_output_seq_cursor(self, task_id: str, seq: int) -> Optional[str]:
        return await self._redis.client.hget(self.output_seq_index_key(task_id), str(seq))

    async def clear_cancel(self, task_id: str) -> None:
        await self._redis.client.delete(self.cancel_key(task_id))

    async def delete_task_resources(self, task_id: str) -> None:
        """Delete Redis keys owned by a task after it is no longer active."""
        await self._redis.client.delete(
            self.meta_key(task_id),
            self.cancel_key(task_id),
            self.output_seq_index_key(task_id),
            f"task:input:{task_id}",
            f"task:output:{task_id}",
        )

    async def dispatch(
            self,
            task_id: str,
            session_id: str,
            run_generation: int,
    ) -> str:
        await self.ensure_consumer_group()
        return await self._redis.client.xadd(
            TASK_DISPATCH_STREAM,
            {
                "task_id": task_id,
                "session_id": session_id,
                "run_generation": str(run_generation),
            },
            maxlen=self._runtime_settings.dispatch_maxlen,
            approximate=True,
        )

    @staticmethod
    def _parse_dispatch_message(
            message,
    ) -> Optional[Tuple[str, str, str, int]]:
        if not message:
            return None
        message_id, fields = message
        task_id = fields.get("task_id") or fields.get(b"task_id")
        session_id = fields.get("session_id") or fields.get(b"session_id")
        run_generation = (
            fields.get("run_generation")
            or fields.get(b"run_generation")
        )
        if isinstance(task_id, bytes):
            task_id = task_id.decode()
        if isinstance(session_id, bytes):
            session_id = session_id.decode()
        if isinstance(run_generation, bytes):
            run_generation = run_generation.decode()
        if not task_id or not session_id or run_generation is None:
            return None
        try:
            parsed_generation = int(run_generation)
        except (TypeError, ValueError):
            return None
        if parsed_generation < 1:
            return None
        return message_id, task_id, session_id, parsed_generation

    async def claim_dispatch(
            self,
            consumer_name: str,
            block_ms: int = 5000,
    ) -> Optional[Tuple[str, str, str, int]]:
        """Claim (message_id, task_id, session_id, run_generation)."""
        await self.ensure_consumer_group()
        try:
            claimed = await self._redis.client.xautoclaim(
                TASK_DISPATCH_STREAM,
                WORKER_CONSUMER_GROUP,
                consumer_name,
                min_idle_time=60000,
                start_id="0-0",
                count=1,
            )
            claimed_messages = claimed[1] if claimed and len(claimed) > 1 else []
            if claimed_messages:
                parsed = self._parse_dispatch_message(claimed_messages[0])
                if parsed:
                    return parsed
        except Exception as exc:
            logger.warning("认领 pending dispatch 失败: %s", exc)

        messages = await self._redis.client.xreadgroup(
            WORKER_CONSUMER_GROUP,
            consumer_name,
            {TASK_DISPATCH_STREAM: ">"},
            count=1,
            block=block_ms,
        )
        if not messages:
            return None
        stream_messages = messages[0][1]
        if not stream_messages:
            return None
        return self._parse_dispatch_message(stream_messages[0])

    async def ack_dispatch(self, message_id: str) -> None:
        await self._redis.client.xack(TASK_DISPATCH_STREAM, WORKER_CONSUMER_GROUP, message_id)

    async def mark_dispatch_failure(
            self,
            message_id: str,
            task_id: str,
            session_id: str,
            run_generation: int,
            error: str,
            *,
            error_code: Optional[str] = None,
            fast_fail: bool = False,
    ) -> bool:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return False
        if int(meta.get("run_generation", 1)) != run_generation:
            if await self.can_ack_stale_dispatch(task_id, run_generation):
                await self.ack_dispatch(message_id)
            return False
        retry_count = int(meta.get("retry_count") or 0) + 1
        updates: Dict[str, Any] = {
            "retry_count": retry_count,
            "last_error": error,
            "updated_at": time.time(),
        }
        if error_code:
            updates["error_code"] = error_code

        max_retries = max(1, self._runtime_settings.task_dispatch_max_retries)
        terminal = fast_fail or retry_count >= max_retries

        if terminal:
            updates["status"] = TaskStatus.FAILED.value
            dlq_fields = {
                "task_id": task_id,
                "session_id": session_id,
                "run_generation": str(run_generation),
                "error": error,
                "retry_count": retry_count,
            }
            if error_code:
                dlq_fields["error_code"] = error_code
            logger.error(
                "任务派发进入 DLQ: task_id=%s session_id=%s retry_count=%s error_code=%s error=%s",
                task_id,
                session_id,
                retry_count,
                error_code or "",
                error,
            )
            try:
                await self._redis.client.xadd(
                    TASK_DISPATCH_DLQ_STREAM,
                    dlq_fields,
                    maxlen=self._runtime_settings.stream_maxlen,
                    approximate=True,
                )
            except Exception as exc:
                logger.warning(
                    "DLQ 写入失败，保留原派发: "
                    "task_id=%s session_id=%s generation=%s error=%s",
                    task_id,
                    session_id,
                    run_generation,
                    exc,
                )
                return False
            changed = await self._mutate_task_meta(
                task_id,
                run_generation,
                updates=updates,
            )
            if changed <= 0:
                await self.ack_dispatch(message_id)
                return False
            await self.ack_dispatch(message_id)
            return True

        logger.warning(
            "任务派发失败，准备重试: task_id=%s session_id=%s retry_count=%s error=%s",
            task_id,
            session_id,
            retry_count,
            error,
        )
        updates["status"] = TaskStatus.PENDING.value
        changed = await self._mutate_task_meta(
            task_id,
            run_generation,
            updates=updates,
        )
        if changed <= 0:
            if await self.can_ack_stale_dispatch(task_id, run_generation):
                await self.ack_dispatch(message_id)
            return False
        next_generation = run_generation + 1
        try:
            replacement_message_id = await self.dispatch(
                task_id,
                session_id,
                next_generation,
            )
        except Exception as exc:
            logger.warning(
                "任务替换派发写入失败，保留原消息: "
                "task_id=%s session_id=%s generation=%s error=%s",
                task_id,
                session_id,
                next_generation,
                exc,
            )
            return False
        advanced_generation = await self.begin_recovery_attempt(
            task_id,
            run_generation,
            durable_dispatch_message_id=replacement_message_id,
        )
        if advanced_generation is None:
            if await self.can_ack_stale_dispatch(task_id, run_generation):
                await self.ack_dispatch(message_id)
            return False
        await self.ack_dispatch(message_id)
        return True

    async def count_dlq_messages(self) -> int:
        try:
            return int(await self._redis.client.xlen(TASK_DISPATCH_DLQ_STREAM))
        except Exception as exc:
            logger.warning("读取 DLQ 积压数量失败: %s", exc)
            return 0

    async def read_dlq_batch(self, count: int = 4) -> list[tuple[str, Dict[str, Any]]]:
        """Return (message_id, fields) pairs from DLQ head."""
        try:
            raw = await self._redis.client.xrange(
                TASK_DISPATCH_DLQ_STREAM,
                min="-",
                max="+",
                count=max(1, count),
            )
        except Exception as exc:
            logger.warning("读取 DLQ 批次失败: %s", exc)
            return []
        parsed: list[tuple[str, Dict[str, Any]]] = []
        for message_id, fields in raw or []:
            mid = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
            normalized: Dict[str, Any] = {}
            for key, value in (fields or {}).items():
                k = key.decode() if isinstance(key, bytes) else str(key)
                v = value.decode() if isinstance(value, bytes) else value
                normalized[k] = v
            parsed.append((mid, normalized))
        return parsed

    async def replay_dlq_entry(self, message_id: str, fields: Dict[str, Any]) -> bool:
        """Re-dispatch a MODEL_* DLQ entry after resetting retry metadata."""
        error_code = str(fields.get("error_code") or "")
        if not error_code.startswith("MODEL_"):
            return False
        task_id = fields.get("task_id")
        session_id = fields.get("session_id")
        if not task_id or not session_id:
            await self._redis.client.xdel(
                TASK_DISPATCH_DLQ_STREAM,
                message_id,
            )
            return False
        try:
            row_generation = int(fields.get("run_generation"))
            row_retry_count = int(fields.get("retry_count"))
        except (TypeError, ValueError):
            await self._redis.client.xdel(
                TASK_DISPATCH_DLQ_STREAM,
                message_id,
            )
            return False
        meta = await self.get_task_meta(task_id)
        valid_identity = bool(
            meta
            and meta.get("status") == TaskStatus.FAILED.value
            and int(meta.get("run_generation", 1)) == row_generation
            and str(meta.get("session_id") or "") == str(session_id)
            and int(meta.get("retry_count") or 0) == row_retry_count
            and str(meta.get("error_code") or "") == error_code
            and str(meta.get("last_error") or "")
            == str(fields.get("error") or "")
        )
        if meta is None:
            return False
        if not valid_identity:
            try:
                current_generation = int(meta.get("run_generation", 1))
            except (TypeError, ValueError):
                return False
            if current_generation > row_generation:
                if await self.can_ack_stale_dispatch(
                    task_id,
                    row_generation,
                ):
                    await self._redis.client.xdel(
                        TASK_DISPATCH_DLQ_STREAM,
                        message_id,
                    )
                return False
            await self._redis.client.xdel(
                TASK_DISPATCH_DLQ_STREAM,
                message_id,
            )
            return False
        run_generation = row_generation
        next_generation = run_generation + 1
        try:
            replacement_message_id = await self.dispatch(
                task_id,
                session_id,
                next_generation,
            )
        except Exception as exc:
            logger.warning(
                "DLQ 替换派发写入失败，保留源条目: "
                "message_id=%s task_id=%s generation=%s error=%s",
                message_id,
                task_id,
                next_generation,
                exc,
            )
            return False
        advanced_generation = await self.begin_recovery_attempt(
            task_id,
            run_generation,
            durable_dispatch_message_id=replacement_message_id,
            expected_failed_identity={
                "status": TaskStatus.FAILED.value,
                "session_id": str(session_id),
                "retry_count": row_retry_count,
                "error_code": error_code,
                "last_error": str(fields.get("error") or ""),
            },
            updates={"retry_count": 0},
            removals=("last_error", "error_code"),
        )
        if advanced_generation is None:
            if await self.can_ack_stale_dispatch(task_id, run_generation):
                await self._redis.client.xdel(
                    TASK_DISPATCH_DLQ_STREAM,
                    message_id,
                )
            return False
        await self._redis.client.xdel(TASK_DISPATCH_DLQ_STREAM, message_id)
        logger.info(
            "DLQ 条目已重放: message_id=%s task_id=%s session_id=%s error_code=%s",
            message_id,
            task_id,
            session_id,
            error_code,
        )
        return True


_task_state: Optional[TaskStateService] = None


def get_task_state() -> TaskStateService:
    global _task_state
    if _task_state is None:
        _task_state = TaskStateService(_task_queue_runtime_settings)
    return _task_state
