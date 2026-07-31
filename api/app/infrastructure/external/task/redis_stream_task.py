#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import uuid
from typing import Optional, Dict

from app.domain.external.message_queue import MessageQueue
from app.domain.external.task import (
    RecoverableTaskInputUnavailable,
    RecoverableTaskReconciliationRequired,
    Task,
    TaskRunner,
)
from app.domain.models.run_outcome import RunStatus
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.task_state import TaskStateService, TaskStatus, get_task_state

logger = logging.getLogger(__name__)


class RedisStreamTask(Task):
    """Distributed task backed by Redis Streams and worker dispatch queue."""

    _run_generation = 1
    _local_executions: Dict[tuple[str, int], asyncio.Task] = {}

    def __init__(
            self,
            task_id: str,
            session_id: str,
            run_generation: int = 1,
            task_runner: Optional[TaskRunner] = None,
            task_state: Optional[TaskStateService] = None,
    ) -> None:
        self._id = task_id
        self._session_id = session_id
        self._run_generation = run_generation
        self._task_runner = task_runner
        self._task_state = task_state or get_task_state()
        self._execution_task: Optional[asyncio.Task] = None
        self._recoverable_error: Optional[RuntimeError] = None

        input_stream_name = f"task:input:{self._id}"
        output_stream_name = f"task:output:{self._id}"

        self._input_stream = RedisStreamMessageQueue(input_stream_name)
        self._output_stream = RedisStreamMessageQueue(output_stream_name)

    @classmethod
    async def create_for_session(
            cls,
            session_id: str,
            task_state: Optional[TaskStateService] = None,
            request_id: Optional[str] = None,
    ) -> "RedisStreamTask":
        task_id = str(uuid.uuid4())
        state = task_state or get_task_state()
        await state.register_task(task_id, session_id, request_id=request_id or "")
        return cls(
            task_id=task_id,
            session_id=session_id,
            run_generation=1,
            task_state=state,
        )

    @classmethod
    def from_task_id(
            cls,
            task_id: str,
            session_id: str = "",
            task_state: Optional[TaskStateService] = None,
            run_generation: int = 1,
    ) -> "RedisStreamTask":
        return cls(
            task_id=task_id,
            session_id=session_id,
            run_generation=run_generation,
            task_state=task_state,
        )

    @classmethod
    async def get(cls, task_id: str) -> Optional["RedisStreamTask"]:
        state = get_task_state()
        meta = await state.get_task_meta(task_id)
        if not meta:
            return None
        return cls.from_task_id(
            task_id,
            meta.get("session_id", ""),
            state,
            int(meta.get("run_generation", 1)),
        )

    @classmethod
    def create(cls, task_runner: TaskRunner) -> "RedisStreamTask":
        raise NotImplementedError(
            "Use create_for_session() in API or worker execution path instead"
        )

    async def dispatch_to_worker(self) -> None:
        snapshot = await self._task_state.get_runtime_snapshot(self._id)
        if snapshot.get("status") != TaskStatus.RUNNING:
            await self._task_state.set_status(
                self._id,
                self._run_generation,
                TaskStatus.PENDING,
            )
        await self._task_state.dispatch(
            self._id,
            self._session_id,
            self._run_generation,
        )
        logger.info(f"任务[{self._id}]已分发到 worker 队列")

    async def invoke(self) -> None:
        """API path: enqueue for worker execution."""
        await self.dispatch_to_worker()

    async def execute_locally(self) -> None:
        """Worker path: run task runner in this process."""
        if not self._task_runner:
            raise RuntimeError(f"任务[{self._id}]缺少 TaskRunner，无法在本地执行")
        execution_key = (self._id, self._run_generation)
        if (
            execution_key in self._local_executions
            and not self._local_executions[execution_key].done()
        ):
            return
        changed = await self._task_state.set_status(
            self._id,
            self._run_generation,
            TaskStatus.RUNNING,
        )
        if not changed:
            raise RecoverableTaskReconciliationRequired(
                f"任务[{self._id}]运行代次已推进，拒绝旧执行启动"
            )
        if (
            execution_key in self._local_executions
            and not self._local_executions[execution_key].done()
        ):
            return
        self._execution_task = asyncio.create_task(self._execute_task())
        self._local_executions[execution_key] = self._execution_task
        logger.info(f"任务[{self._id}]在 worker 中开始执行")
        await self._execution_task

    def cancel(self) -> bool:
        asyncio.create_task(self._task_state.request_cancel(self._id))
        if self._execution_task and not self._execution_task.done():
            self._execution_task.cancel()
        return True

    async def _clear_reconciliation_after_mapping(self) -> None:
        try:
            await self._task_state.clear_run_reconciliation(
                self._id,
                self._run_generation,
            )
        except asyncio.CancelledError:
            logger.warning(
                "任务[%s]已映射权威结果，对账清理被取消",
                self._id,
            )
        except Exception as exc:
            logger.warning(
                "任务[%s]清理已对账结果失败: %s",
                self._id,
                exc,
            )

    async def _execute_task(self) -> None:
        self._recoverable_error = None
        mapped_outcome = False
        legacy_completion = False
        try:
            outcome = await self._task_runner.invoke(self)
            if outcome is not None:
                mapped_outcome = True
                status = RunStatus(outcome.status)
                task_status = {
                    RunStatus.SUCCEEDED: TaskStatus.DONE,
                    RunStatus.FAILED: TaskStatus.FAILED,
                    RunStatus.CANCELLED: TaskStatus.CANCELLED,
                    RunStatus.WAITING: TaskStatus.PENDING,
                }[status]
                try:
                    changed = await self._task_state.set_status(
                        self._id,
                        self._run_generation,
                        task_status,
                    )
                    if not changed:
                        raise RecoverableTaskReconciliationRequired(
                            f"任务[{self._id}]运行代次已推进，拒绝旧结果映射"
                        )
                except asyncio.CancelledError as exc:
                    raise RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]权威结果尚未映射到任务状态"
                    ) from exc
                except Exception as exc:
                    raise RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]权威结果尚未映射到任务状态"
                    ) from exc
                await self._clear_reconciliation_after_mapping()
            else:
                legacy_completion = True
        except (
            RecoverableTaskInputUnavailable,
            RecoverableTaskReconciliationRequired,
        ) as e:
            mapped_outcome = True
            self._recoverable_error = e
            logger.warning("任务[%s]等待恢复对账: %s", self._id, e)
            try:
                changed = await self._task_state.set_status(
                    self._id,
                    self._run_generation,
                    TaskStatus.PENDING,
                )
                if not changed:
                    raise RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]运行代次已推进，等待新执行对账"
                    )
            except asyncio.CancelledError as exc:
                raise RecoverableTaskReconciliationRequired(
                    f"任务[{self._id}]状态服务不可用，等待恢复对账"
                ) from exc
            except Exception as exc:
                raise RecoverableTaskReconciliationRequired(
                    f"任务[{self._id}]状态服务不可用，等待恢复对账"
                ) from exc
        except asyncio.CancelledError:
            mapped_outcome = True
            logger.info(f"任务[{self._id}]执行被取消")
            try:
                changed = await self._task_state.set_status(
                    self._id,
                    self._run_generation,
                    TaskStatus.CANCELLED,
                )
                if not changed:
                    raise RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]运行代次已推进，拒绝旧取消映射"
                    )
            except Exception as exc:
                reconciliation_error = (
                    RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]取消结果尚未映射到任务状态"
                    )
                )
                try:
                    await self._task_state.set_status(
                        self._id,
                        self._run_generation,
                        TaskStatus.PENDING,
                    )
                except Exception as pending_exc:
                    raise RecoverableTaskReconciliationRequired(
                        f"任务[{self._id}]状态服务不可用，等待恢复取消结果"
                    ) from pending_exc
                raise reconciliation_error from exc
            await self._clear_reconciliation_after_mapping()
            raise
        except Exception as e:
            mapped_outcome = True
            logger.exception(f"任务[{self._id}]执行出现异常: {str(e)}")
            changed = await self._task_state.set_status(
                self._id,
                self._run_generation,
                TaskStatus.FAILED,
            )
            if not changed:
                raise RecoverableTaskReconciliationRequired(
                    f"任务[{self._id}]运行代次已推进，拒绝旧失败映射"
                ) from e
            raise
        finally:
            if self._task_runner:
                await self._task_runner.on_done(self)
            if legacy_completion and not mapped_outcome:
                status = await self._task_state.get_status(self._id)
                if status not in {TaskStatus.PENDING, TaskStatus.CANCELLED, TaskStatus.FAILED}:
                    await self._task_state.set_status(
                        self._id,
                        self._run_generation,
                        TaskStatus.DONE,
                    )
            self._local_executions.pop(
                (self._id, self._run_generation),
                None,
            )

    @property
    def input_stream(self) -> MessageQueue:
        return self._input_stream

    @property
    def recoverable_error(self) -> Optional[RuntimeError]:
        return self._recoverable_error

    @property
    def output_stream(self) -> MessageQueue:
        return self._output_stream

    @property
    def id(self) -> str:
        return self._id

    @property
    def run_generation(self) -> int:
        return self._run_generation

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
        for _execution_key, execution in list(cls._local_executions.items()):
            execution.cancel()
        cls._local_executions.clear()
