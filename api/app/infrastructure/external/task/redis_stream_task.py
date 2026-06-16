#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import uuid
from typing import Optional, Dict

from app.domain.external.message_queue import MessageQueue
from app.domain.external.task import Task, TaskRunner
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.task_state import TaskStateService, TaskStatus, get_task_state

logger = logging.getLogger(__name__)


class RedisStreamTask(Task):
    """Distributed task backed by Redis Streams and worker dispatch queue."""

    _local_executions: Dict[str, asyncio.Task] = {}

    def __init__(
            self,
            task_id: str,
            session_id: str,
            task_runner: Optional[TaskRunner] = None,
            task_state: Optional[TaskStateService] = None,
    ) -> None:
        self._id = task_id
        self._session_id = session_id
        self._task_runner = task_runner
        self._task_state = task_state or get_task_state()
        self._execution_task: Optional[asyncio.Task] = None

        input_stream_name = f"task:input:{self._id}"
        output_stream_name = f"task:output:{self._id}"

        self._input_stream = RedisStreamMessageQueue(input_stream_name)
        self._output_stream = RedisStreamMessageQueue(output_stream_name)

    @classmethod
    async def create_for_session(
            cls,
            session_id: str,
            task_state: Optional[TaskStateService] = None,
    ) -> "RedisStreamTask":
        task_id = str(uuid.uuid4())
        state = task_state or get_task_state()
        await state.register_task(task_id, session_id)
        return cls(task_id=task_id, session_id=session_id, task_state=state)

    @classmethod
    def from_task_id(
            cls,
            task_id: str,
            session_id: str = "",
            task_state: Optional[TaskStateService] = None,
    ) -> "RedisStreamTask":
        return cls(task_id=task_id, session_id=session_id, task_state=task_state)

    @classmethod
    async def get(cls, task_id: str) -> Optional["RedisStreamTask"]:
        state = get_task_state()
        meta = await state.get_task_meta(task_id)
        if not meta:
            return None
        return cls.from_task_id(task_id, meta.get("session_id", ""), state)

    @classmethod
    def create(cls, task_runner: TaskRunner) -> "RedisStreamTask":
        raise NotImplementedError(
            "Use create_for_session() in API or worker execution path instead"
        )

    async def dispatch_to_worker(self) -> None:
        snapshot = await self._task_state.get_runtime_snapshot(self._id)
        if snapshot.get("status") != TaskStatus.RUNNING:
            await self._task_state.set_status(self._id, TaskStatus.PENDING)
        await self._task_state.dispatch(self._id, self._session_id)
        logger.info(f"任务[{self._id}]已分发到 worker 队列")

    async def invoke(self) -> None:
        """API path: enqueue for worker execution."""
        await self.dispatch_to_worker()

    async def execute_locally(self) -> None:
        """Worker path: run task runner in this process."""
        if not self._task_runner:
            raise RuntimeError(f"任务[{self._id}]缺少 TaskRunner，无法在本地执行")
        if self._id in self._local_executions and not self._local_executions[self._id].done():
            return
        self._execution_task = asyncio.create_task(self._execute_task())
        self._local_executions[self._id] = self._execution_task
        await self._task_state.set_status(self._id, TaskStatus.RUNNING)
        logger.info(f"任务[{self._id}]在 worker 中开始执行")
        await self._execution_task

    def cancel(self) -> bool:
        asyncio.create_task(self._task_state.request_cancel(self._id))
        if self._execution_task and not self._execution_task.done():
            self._execution_task.cancel()
        return True

    async def _execute_task(self) -> None:
        try:
            await self._task_runner.invoke(self)
        except asyncio.CancelledError:
            logger.info(f"任务[{self._id}]执行被取消")
            await self._task_state.set_status(self._id, TaskStatus.CANCELLED)
            raise
        except Exception as e:
            logger.exception(f"任务[{self._id}]执行出现异常: {str(e)}")
            await self._task_state.set_status(self._id, TaskStatus.FAILED)
            raise
        finally:
            if self._task_runner:
                await self._task_runner.on_done(self)
            status = await self._task_state.get_status(self._id)
            if status not in {TaskStatus.CANCELLED, TaskStatus.FAILED}:
                await self._task_state.set_status(self._id, TaskStatus.DONE)
            self._local_executions.pop(self._id, None)

    @property
    def input_stream(self) -> MessageQueue:
        return self._input_stream

    @property
    def output_stream(self) -> MessageQueue:
        return self._output_stream

    @property
    def id(self) -> str:
        return self._id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def done(self) -> bool:
        if self._execution_task is not None:
            return self._execution_task.done()
        return False

    async def is_done(self) -> bool:
        return await self._task_state.is_done(self._id)

    @classmethod
    async def destroy_task_resources(
            cls,
            task_id: str,
            task_state: Optional[TaskStateService] = None,
    ) -> None:
        state = task_state or get_task_state()
        await state.delete_task_resources(task_id)

    @classmethod
    async def destroy(cls) -> None:
        for task_id, execution in list(cls._local_executions.items()):
            execution.cancel()
        cls._local_executions.clear()
