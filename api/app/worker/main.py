#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone agent worker: consumes task dispatch queue and runs AgentTaskRunner."""
import asyncio
import logging
import signal
import socket
import uuid

from app.application.services.bootstrap_service import bootstrap_data
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.container import get_worker_container, init_worker_container, shutdown_worker_container
from app.domain.external.sandbox import Sandbox
from app.domain.models.session import SessionStatus
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.runtime_settings import get_admission_runtime_settings
from app.infrastructure.external.sandbox.admission import get_sandbox_quota
from app.infrastructure.external.sandbox.sandbox_maintenance import run_sandbox_maintenance
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_lease import (
    release_task_lease,
    try_acquire_task_lease,
)
from app.infrastructure.external.task.task_state import TaskStatus, get_task_state
from app.infrastructure.logging import setup_logging
from app.infrastructure.storage.postgres import get_uow
from app.runtime_role import ProcessRole, set_role
from core.config import get_settings

set_role(ProcessRole.WORKER)

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 30


async def _sandbox_cleanup_loop() -> None:
    from app.application.services.config_provider import get_runtime_config

    interval = max(30, get_runtime_config().sandbox.cleanup_interval_seconds)
    while True:
        try:
            removed = await run_sandbox_maintenance()
            if removed:
                logger.info("Worker 沙箱维护回收数量: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Worker 沙箱维护失败: %s", exc)
        await asyncio.sleep(interval)


async def _startup_reconcile() -> None:
    from app.infrastructure.external.sandbox.sandbox_driver import get_sandbox_class

    sandbox_cls = get_sandbox_class()
    live_ids = await sandbox_cls.list_live_sandbox_ids()
    await get_sandbox_quota().reconcile(live_ids)
    logger.info("启动 reconcile 完成: live_sandboxes=%s", len(live_ids))


class AgentWorker:
    def __init__(
            self,
            runner_factory: TaskRunnerFactory,
            file_storage: CosFileStorage,
            sandbox_cls: type[Sandbox],
            task_cls: type[RedisStreamTask],
    ) -> None:
        self._settings = get_settings()
        self._task_state = get_task_state()
        self._consumer_name = f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._running = True
        from app.application.services.config_provider import get_runtime_config

        self._max_concurrent = max(1, get_runtime_config().worker.max_concurrent_tasks)
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._active_tasks: set[asyncio.Task] = set()
        self._runner_factory = runner_factory
        self._file_storage = file_storage
        self._sandbox_cls = sandbox_cls
        self._task_cls = task_cls
        self._quota = get_sandbox_quota()

    async def start(self) -> None:
        await self._task_state.ensure_consumer_group()
        admission = get_admission_runtime_settings()
        logger.info(
            "Agent worker 启动: consumer=%s max_concurrent=%s node=%s",
            self._consumer_name,
            self._max_concurrent,
            self._quota.node_id,
        )
        while self._running:
            try:
                if not await self._quota.can_admit():
                    await asyncio.sleep(admission.admission_poll_interval_seconds)
                    continue
                claim = await self._task_state.claim_dispatch(
                    self._consumer_name,
                    block_ms=5000,
                )
                if claim is None:
                    continue
                await self._semaphore.acquire()
                try:
                    message_id, task_id, session_id = claim
                    task = asyncio.create_task(
                        self._handle_claimed_job(message_id, task_id, session_id),
                    )
                    self._active_tasks.add(task)
                    task.add_done_callback(self._on_job_task_done)
                except Exception:
                    self._semaphore.release()
                    raise
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Worker 循环异常: %s", exc)
                await asyncio.sleep(1)

        if self._active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks, return_exceptions=True),
                    timeout=SHUTDOWN_GRACE_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Worker 优雅停机超时，仍有 %s 个活跃任务",
                    len(self._active_tasks),
                )

    def _on_job_task_done(self, task: asyncio.Task) -> None:
        self._active_tasks.discard(task)
        self._semaphore.release()

    async def _handle_claimed_job(
            self,
            message_id: str,
            task_id: str,
            session_id: str,
    ) -> None:
        admission = get_admission_runtime_settings()
        lease_acquired = await try_acquire_task_lease(
            task_id,
            admission.task_execution_lease_seconds,
        )
        if not lease_acquired:
            logger.warning(
                "任务执行租约冲突，跳过重复执行: task_id=%s session_id=%s",
                task_id,
                session_id,
            )
            await self._task_state.ack_dispatch(message_id)
            return
        try:
            await self._execute_job(task_id, session_id)
            await self._task_state.ack_dispatch(message_id)
        except Exception as exc:
            logger.exception(
                "Worker 执行任务失败: task_id=%s session_id=%s error=%s",
                task_id,
                session_id,
                exc,
            )
            await self._task_state.mark_dispatch_failure(
                message_id=message_id,
                task_id=task_id,
                session_id=session_id,
                error=str(exc),
            )
        finally:
            await release_task_lease(task_id)

    async def _execute_job(self, task_id: str, session_id: str) -> None:
        if await self._task_state.is_cancelled(task_id):
            await self._task_state.set_status(task_id, TaskStatus.CANCELLED)
            return

        meta = await self._task_state.get_task_meta(task_id) or {}
        if meta.get("task_type") == "codebase_ingest":
            await self._execute_ingest_job(task_id, meta.get("resource_id", ""))
            return

        async with get_uow() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            logger.error("Worker 找不到会话: session_id=%s task_id=%s", session_id, task_id)
            await self._task_state.set_status(task_id, TaskStatus.FAILED)
            raise RuntimeError(f"任务会话不存在: {session_id}")

        async with get_uow() as uow:
            await uow.session.update_status(session_id, SessionStatus.RUNNING)

        runner = await self._runner_factory.create_runner(session)
        task = self._task_cls(
            task_id=task_id,
            session_id=session_id,
            task_runner=runner,
        )

        async def cancel_watcher() -> None:
            while not await task.is_done():
                if await self._task_state.wait_for_cancel(task_id, timeout_seconds=5.0):
                    task.cancel()
                    break

        watcher = asyncio.create_task(cancel_watcher())
        try:
            await task.execute_locally()
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            await runner.cleanup()
            container = get_worker_container()
            await container.mcp_connection_pool().release_stale()
            await container.a2a_connection_pool().release_stale()

    async def _execute_ingest_job(self, task_id: str, codebase_id: str) -> None:
        if not codebase_id:
            await self._task_state.set_status(task_id, TaskStatus.FAILED)
            raise RuntimeError("代码库摄取任务缺少 resource_id")
        runner = CodebaseIngestionTaskRunner(
            uow_factory=get_uow,
            sandbox_cls=self._sandbox_cls,
            file_storage=self._file_storage,
            codebase_id=codebase_id,
        )
        task = self._task_cls(
            task_id=task_id,
            session_id=f"codebase-ingest:{codebase_id}",
            task_runner=runner,
        )
        await task.execute_locally()

    def request_shutdown(self) -> None:
        self._running = False


async def main() -> None:
    setup_logging()
    container = await init_worker_container()
    from app.application.services.skill_service import SkillService

    await bootstrap_data(
        uow_factory=get_uow,
        skill_service=SkillService(uow_factory=get_uow),
    )

    await _startup_reconcile()
    sandbox_cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())
    worker = AgentWorker(
        runner_factory=await container.task_runner_factory(),
        file_storage=await container.file_storage(),
        sandbox_cls=container.sandbox_cls(),
        task_cls=container.task_cls(),
    )
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Worker 收到停机信号")
        worker.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        await worker.start()
    finally:
        sandbox_cleanup_task.cancel()
        try:
            await sandbox_cleanup_task
        except asyncio.CancelledError:
            pass
        await RedisStreamTask.destroy()
        await shutdown_worker_container(container)


if __name__ == "__main__":
    asyncio.run(main())
