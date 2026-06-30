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
WORKER_CONSUMER_GROUP = "manus-workers"
TASK_META_TTL_SECONDS = 86400 * 7
OUTPUT_SEQ_INDEX_PREFIX = "task:output:seq:"
CANCEL_NOTIFY_CHANNEL_PREFIX = "task:cancel:notify:"


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
    """Manage task lifecycle metadata in Redis for multi-process deployment."""

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
    ) -> None:
        payload = {
            "task_id": task_id,
            "session_id": session_id,
            "task_type": task_type,
            "resource_id": resource_id,
            "request_id": request_id or "",
            "status": TaskStatus.PENDING.value,
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

    async def set_status(self, task_id: str, status: TaskStatus) -> None:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return
        meta["status"] = status.value
        meta["updated_at"] = time.time()
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(meta),
            ex=TASK_META_TTL_SECONDS,
        )

    async def get_status(self, task_id: str) -> Optional[TaskStatus]:
        meta = await self.get_task_meta(task_id)
        if not meta or not meta.get("status"):
            return None
        return TaskStatus(meta["status"])

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
        }

    async def record_heartbeat(self, task_id: str, worker_id: str) -> None:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return
        now = time.time()
        meta["last_heartbeat_at"] = now
        meta["worker_id"] = worker_id
        meta["updated_at"] = now
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(meta),
            ex=TASK_META_TTL_SECONDS,
        )

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
        await self.set_status(task_id, TaskStatus.CANCELLED)
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

    async def dispatch(self, task_id: str, session_id: str) -> str:
        await self.ensure_consumer_group()
        return await self._redis.client.xadd(
            TASK_DISPATCH_STREAM,
            {"task_id": task_id, "session_id": session_id},
            maxlen=self._runtime_settings.dispatch_maxlen,
            approximate=True,
        )

    @staticmethod
    def _parse_dispatch_message(message) -> Optional[Tuple[str, str, str]]:
        if not message:
            return None
        message_id, fields = message
        task_id = fields.get("task_id") or fields.get(b"task_id")
        session_id = fields.get("session_id") or fields.get(b"session_id")
        if isinstance(task_id, bytes):
            task_id = task_id.decode()
        if isinstance(session_id, bytes):
            session_id = session_id.decode()
        if not task_id or not session_id:
            return None
        return message_id, task_id, session_id

    async def claim_dispatch(
            self,
            consumer_name: str,
            block_ms: int = 5000,
    ) -> Optional[Tuple[str, str, str]]:
        """Claim one dispatch job. Returns (message_id, task_id, session_id)."""
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
            error: str,
            *,
            error_code: Optional[str] = None,
            fast_fail: bool = False,
    ) -> None:
        meta = await self.get_task_meta(task_id) or {
            "task_id": task_id,
            "session_id": session_id,
            "status": TaskStatus.PENDING.value,
            "retry_count": 0,
        }
        retry_count = int(meta.get("retry_count") or 0) + 1
        meta["retry_count"] = retry_count
        meta["last_error"] = error
        if error_code:
            meta["error_code"] = error_code

        max_retries = max(1, self._runtime_settings.task_dispatch_max_retries)
        terminal = fast_fail or retry_count >= max_retries

        if terminal:
            meta["status"] = TaskStatus.FAILED.value
            dlq_fields = {
                "task_id": task_id,
                "session_id": session_id,
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
            await self._redis.client.xadd(
                TASK_DISPATCH_DLQ_STREAM,
                dlq_fields,
                maxlen=self._runtime_settings.stream_maxlen,
                approximate=True,
            )
            await self._redis.client.set(
                self.meta_key(task_id),
                json.dumps(meta),
                ex=TASK_META_TTL_SECONDS,
            )
            await self.ack_dispatch(message_id)
            return

        logger.warning(
            "任务派发失败，准备重试: task_id=%s session_id=%s retry_count=%s error=%s",
            task_id,
            session_id,
            retry_count,
            error,
        )
        meta["status"] = TaskStatus.PENDING.value
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(meta),
            ex=TASK_META_TTL_SECONDS,
        )
        await self.ack_dispatch(message_id)
        await self.dispatch(task_id, session_id)

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
            return False
        meta = await self.get_task_meta(task_id) or {
            "task_id": task_id,
            "session_id": session_id,
            "status": TaskStatus.PENDING.value,
        }
        meta["retry_count"] = 0
        meta["status"] = TaskStatus.PENDING.value
        meta.pop("last_error", None)
        meta.pop("error_code", None)
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(meta),
            ex=TASK_META_TTL_SECONDS,
        )
        await self._redis.client.xdel(TASK_DISPATCH_DLQ_STREAM, message_id)
        await self.dispatch(task_id, session_id)
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
