#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone agent worker: consumes task dispatch queue and runs AgentTaskRunner."""
import asyncio
import logging
import os
import signal
import socket
import uuid

from app.application.services.bootstrap_service import bootstrap_data
from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_service import MemoryService
from app.application.services.skill_service import SkillService
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import TaskStatus, get_task_state
from app.infrastructure.logging import setup_logging
from app.application.services.config_provider import get_app_config_provider, get_runtime_config
from app.infrastructure.external.tools.connection_pool import A2AConnectionPool, MCPConnectionPool
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.storage.cos import get_cos
from app.infrastructure.storage.postgres import get_postgres, get_uow
from app.infrastructure.storage.redis import get_redis
from app.domain.models.app_config import AgentConfig, A2AConfig, MCPConfig
from app.domain.models.session import SessionStatus
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from core.config import get_settings

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 30


async def _sandbox_cleanup_loop() -> None:
    from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
    from app.application.services.config_provider import get_runtime_config

    interval = max(60, get_runtime_config().sandbox.cleanup_interval_seconds)
    while True:
        try:
            removed = await DockerSandbox.cleanup_orphaned_containers()
            if removed:
                logger.info("Worker 清理孤儿沙箱容器数量: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Worker 清理孤儿沙箱容器失败: %s", exc)
        await asyncio.sleep(interval)


class AgentWorker:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._task_state = get_task_state()
        self._consumer_name = f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._running = True
        self._max_concurrent = max(1, get_runtime_config().worker.max_concurrent_tasks)
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._active_tasks: set[asyncio.Task] = set()
        self._config_provider = get_app_config_provider()
        cipher = ApiKeyCipher(self._settings.api_key_secret)
        llm_model_service = LLMModelService(uow_factory=get_uow, cipher=cipher)
        skill_service = SkillService(uow_factory=get_uow)
        memory_service = MemoryService(uow_factory=get_uow)
        file_storage = CosFileStorage(
            bucket=self._settings.cos_bucket,
            cos=get_cos(),
            uow_factory=get_uow,
        )
        checkpoint_service = CheckpointService(
            uow_factory=get_uow,
            cos=get_cos(),
            sandbox_cls=DockerSandbox,
        )
        self._runner_factory = TaskRunnerFactory(
            uow_factory=get_uow,
            llm_model_service=llm_model_service,
            skill_service=skill_service,
            memory_service=memory_service,
            agent_config=AgentConfig(),
            mcp_config=MCPConfig(),
            a2a_config=A2AConfig(),
            sandbox_cls=DockerSandbox,
            json_parser=RepairJSONParser(),
            search_engine=BingSearchEngine(),
            file_storage=file_storage,
            auto_extract_memory=get_runtime_config().memory.auto_extract_enabled,
            config_provider=self._config_provider,
            checkpoint_service=checkpoint_service,
        )

    async def start(self) -> None:
        await self._task_state.ensure_consumer_group()
        logger.info(
            "Agent worker 启动: consumer=%s max_concurrent=%s",
            self._consumer_name,
            self._max_concurrent,
        )
        while self._running:
            try:
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
        task = RedisStreamTask(
            task_id=task_id,
            session_id=session_id,
            task_runner=runner,
        )

        async def cancel_watcher() -> None:
            while not await task.is_done():
                if await self._task_state.is_cancelled(task_id):
                    task.cancel()
                    break
                await asyncio.sleep(0.5)

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
            await MCPConnectionPool.release_stale()
            await A2AConnectionPool.release_stale()

    async def _execute_ingest_job(self, task_id: str, codebase_id: str) -> None:
        if not codebase_id:
            await self._task_state.set_status(task_id, TaskStatus.FAILED)
            raise RuntimeError("代码库摄取任务缺少 resource_id")
        file_storage = CosFileStorage(
            bucket=self._settings.cos_bucket,
            cos=get_cos(),
            uow_factory=get_uow,
        )
        runner = CodebaseIngestionTaskRunner(
            uow_factory=get_uow,
            sandbox_cls=DockerSandbox,
            file_storage=file_storage,
            codebase_id=codebase_id,
        )
        task = RedisStreamTask(task_id=task_id, session_id=f"codebase-ingest:{codebase_id}", task_runner=runner)
        await task.execute_locally()

    async def shutdown(self) -> None:
        self._running = False
        from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

        await get_sandbox_pool().shutdown()
        await RedisStreamTask.destroy()
        await get_redis().shutdown()
        await get_postgres().shutdown()
        await get_cos().shutdown()


async def main() -> None:
    os.environ["MANUS_PROCESS_ROLE"] = "worker"
    setup_logging()
    settings = get_settings()
    # 必须先初始化基础设施(Postgres/Redis/Cos)，再构造 AgentWorker，
    # 因为 AgentWorker.__init__ 会立即创建依赖数据库会话工厂的组件(如 CosFileStorage)。
    await get_redis().init()
    await get_postgres().init()
    await get_cos().init()
    from app.infrastructure.external.event_seq_allocator import sync_global_event_seq

    await sync_global_event_seq()
    from app.application.services.config_provider import get_app_config_provider

    await get_app_config_provider().get()
    from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

    await get_sandbox_pool().start()
    from app.infrastructure.external.app_config_notifier import start_config_invalidate_listener

    await start_config_invalidate_listener()
    await bootstrap_data(
        uow_factory=get_uow,
        skill_service=SkillService(uow_factory=get_uow),
    )

    sandbox_cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())
    worker = AgentWorker()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Worker 收到停机信号")
        worker._running = False

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
        from app.infrastructure.external.app_config_notifier import stop_config_invalidate_listener

        await stop_config_invalidate_listener()
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
