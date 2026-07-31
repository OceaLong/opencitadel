#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared ingest-task progress/drain/dispatch helpers.

``CodebaseService`` and ``KnowledgeBaseService`` both track a single
"ingest task" per resource (a codebase build or a knowledge base build) and
need to: check whether that task still looks alive, wait for a cancelled
task to actually drain before deleting the owning resource, and dispatch a
freshly-registered task to a worker over the Redis stream queue. The three
implementations were verbatim duplicates apart from resource-specific
naming, so they live here once and are mixed into both services.

Concrete classes using this mixin must set the following attributes
(typically in ``__init__``) before any of these methods are called:

- ``_task_state``: a ``TaskStatePort``-compatible object.
- ``_ingest_terminal_statuses``: a container of resource statuses that mean
  "ingest is not in progress" (e.g. READY/FAILED for that resource type).
- ``_ingest_resource_label``: the Chinese noun used in log messages for the
  resource (e.g. "代码库" / "知识库"). Only affects log text, substituted so
  the rendered message is byte-identical to the pre-extraction wording.
- ``_ingest_session_prefix``: the worker session-id prefix for this
  resource type (e.g. "codebase-ingest" / "kb-ingest").
- ``_ingest_task_type``: the task type recorded with the task state (e.g.
  "codebase_ingest" / "kb_ingest").
"""
import asyncio
import logging
import time
from typing import Any, Container

from app.infrastructure.external.task.redis_stream_task import RedisStreamTask

logger = logging.getLogger(__name__)

_INGEST_STALE_AFTER_SECONDS = 120.0
_DELETE_TASK_DRAIN_TIMEOUT_SECONDS = 30.0
_DELETE_TASK_POLL_INTERVAL_SECONDS = 0.5


class IngestTaskSupport:
    """Mixin: ingest-in-progress check, drain wait, and task dispatch."""

    _ingest_terminal_statuses: Container[Any]
    _ingest_resource_label: str
    _ingest_session_prefix: str
    _ingest_task_type: str

    async def _ingest_in_progress(self, resource: Any) -> bool:
        if not resource.ingest_task_id:
            return False
        if resource.status in self._ingest_terminal_statuses:
            return False
        meta = await self._task_state.get_task_meta(resource.ingest_task_id)
        if not meta:
            return False
        if await self._task_state.is_done(resource.ingest_task_id):
            return False
        if self._task_state.heartbeat_is_stale(meta, _INGEST_STALE_AFTER_SECONDS):
            return False
        return True

    async def _wait_for_task_drain(self, task_id: str) -> None:
        deadline = time.monotonic() + _DELETE_TASK_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            snapshot = await self._task_state.get_runtime_snapshot(task_id)
            if snapshot.get("is_done"):
                return
            await asyncio.sleep(_DELETE_TASK_POLL_INTERVAL_SECONDS)
        logger.warning(
            "等待%s摄取任务结束超时 task_id=%s",
            self._ingest_resource_label,
            task_id,
        )

    async def _dispatch_ingest_task(self, task_id: str, *, resource_id: str) -> None:
        session_id = f"{self._ingest_session_prefix}:{resource_id}"
        await self._task_state.register_task(
            task_id,
            session_id=session_id,
            task_type=self._ingest_task_type,
            resource_id=resource_id,
        )
        task = RedisStreamTask(task_id=task_id, session_id=session_id)
        await task.dispatch_to_worker()
