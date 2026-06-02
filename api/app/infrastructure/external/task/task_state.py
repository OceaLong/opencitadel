#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis-backed distributed task metadata, dispatch queue, and cancel control."""
import json
import logging
import uuid
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

TASK_META_PREFIX = "task:meta:"
TASK_CANCEL_PREFIX = "task:cancel:"
TASK_DISPATCH_STREAM = "task:dispatch"
WORKER_CONSUMER_GROUP = "manus-workers"
TASK_META_TTL_SECONDS = 86400 * 7


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskStateService:
    """Manage task lifecycle metadata in Redis for multi-process deployment."""

    def __init__(self) -> None:
        self._redis = get_redis()

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

    async def register_task(self, task_id: str, session_id: str) -> None:
        payload = {
            "task_id": task_id,
            "session_id": session_id,
            "status": TaskStatus.PENDING.value,
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
        await self._redis.client.set(
            self.meta_key(task_id),
            json.dumps(meta),
            ex=TASK_META_TTL_SECONDS,
        )

    async def is_done(self, task_id: str) -> bool:
        meta = await self.get_task_meta(task_id)
        if not meta:
            return True
        return meta.get("status") in {TaskStatus.DONE.value, TaskStatus.CANCELLED.value}

    async def request_cancel(self, task_id: str) -> None:
        await self._redis.client.set(self.cancel_key(task_id), "1", ex=3600)
        await self.set_status(task_id, TaskStatus.CANCELLED)

    async def is_cancelled(self, task_id: str) -> bool:
        return bool(await self._redis.client.get(self.cancel_key(task_id)))

    async def clear_cancel(self, task_id: str) -> None:
        await self._redis.client.delete(self.cancel_key(task_id))

    async def dispatch(self, task_id: str, session_id: str) -> str:
        await self.ensure_consumer_group()
        return await self._redis.client.xadd(
            TASK_DISPATCH_STREAM,
            {"task_id": task_id, "session_id": session_id},
        )

    async def claim_dispatch(
            self,
            consumer_name: str,
            block_ms: int = 5000,
    ) -> Optional[Tuple[str, str, str]]:
        """Claim one dispatch job. Returns (message_id, task_id, session_id)."""
        await self.ensure_consumer_group()
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
        message_id, fields = stream_messages[0]
        task_id = fields.get("task_id") or fields.get(b"task_id")
        session_id = fields.get("session_id") or fields.get(b"session_id")
        if isinstance(task_id, bytes):
            task_id = task_id.decode()
        if isinstance(session_id, bytes):
            session_id = session_id.decode()
        return message_id, task_id, session_id

    async def ack_dispatch(self, message_id: str) -> None:
        await self._redis.client.xack(TASK_DISPATCH_STREAM, WORKER_CONSUMER_GROUP, message_id)


_task_state: Optional[TaskStateService] = None


def get_task_state() -> TaskStateService:
    global _task_state
    if _task_state is None:
        _task_state = TaskStateService()
    return _task_state
