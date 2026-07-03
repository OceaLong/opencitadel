#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone agent worker: consumes task dispatch queue and runs AgentTaskRunner."""
import asyncio
import logging
import signal
import socket
import uuid

from app.application.services.bootstrap_service import bootstrap_data
from app.application.services.config_provider import get_runtime_config
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.container import get_worker_container, init_worker_container, shutdown_worker_container
from app.domain.external.sandbox import Sandbox
from app.domain.models.event import MessageEvent
from app.domain.models.session import SessionStatus
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from app.domain.services.knowledge_base.ingestion_task_runner import KBIngestionTaskRunner
from app.domain.external.file_storage import FileStorage
from app.infrastructure.external.runtime_settings import get_admission_runtime_settings
from app.infrastructure.external.sandbox.admission import get_sandbox_quota
from app.infrastructure.external.sandbox.sandbox_maintenance import run_sandbox_maintenance
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_lease import (
    get_task_lease_owner,
    get_worker_id,
    release_task_lease,
    renew_task_lease,
    try_acquire_task_lease,
)
from app.application.services.recoverable_task_retry import (
    prepare_recoverable_retry,
    requeue_latest_user_message,
)
from app.domain.models.error_codes import MODEL_UNAVAILABLE
from app.domain.utils.llm_retry import classify_llm_error_code
from app.infrastructure.external.llm.circuit_breaker import get_llm_circuit_breaker
from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError, create_resilient_llm
from app.infrastructure.external.task.task_state import TaskStatus, get_task_state
from app.infrastructure.logging import setup_logging
from app.infrastructure.observability.logging_context import bind_context, configure_structured_logging
from app.infrastructure.storage.postgres import get_uow
from app.runtime_role import ProcessRole, set_role
from core.config import get_settings

set_role(ProcessRole.WORKER)

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 30
TASK_RECONCILE_INTERVAL_SECONDS = 30


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
            checkpoint_service: CheckpointService,
            file_storage: FileStorage,
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
        self._checkpoint_service = checkpoint_service
        self._file_storage = file_storage
        self._sandbox_cls = sandbox_cls
        self._task_cls = task_cls
        self._quota = get_sandbox_quota()

    async def start(self) -> None:
        await self._task_state.ensure_consumer_group()
        admission = get_admission_runtime_settings()
        await self._reconcile_orphaned_tasks("startup")
        reconcile_task = asyncio.create_task(self._task_reconcile_loop())
        runtime = get_runtime_config()
        dlq_replay_task = None
        if runtime.model_resilience.dlq_replay_enabled:
            dlq_replay_task = asyncio.create_task(self._dlq_replay_loop())
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

        reconcile_task.cancel()
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass
        if dlq_replay_task is not None:
            dlq_replay_task.cancel()
            try:
                await dlq_replay_task
            except asyncio.CancelledError:
                pass

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

    async def _task_reconcile_loop(self) -> None:
        while self._running:
            await asyncio.sleep(TASK_RECONCILE_INTERVAL_SECONDS)
            await self._reconcile_orphaned_tasks("periodic")

    async def _dlq_replay_loop(self) -> None:
        while self._running:
            runtime = get_runtime_config()
            cfg = runtime.model_resilience
            await asyncio.sleep(max(1, cfg.dlq_replay_interval_seconds))
            if not cfg.dlq_replay_enabled:
                continue
            try:
                batch = await self._task_state.read_dlq_batch(cfg.dlq_replay_batch_size)
                if not batch:
                    continue
                for message_id, fields in batch:
                    error_code = str(fields.get("error_code") or "")
                    if not error_code.startswith("MODEL_"):
                        continue
                    session_id = fields.get("session_id")
                    model_id = None
                    if session_id:
                        async with get_uow() as uow:
                            session = await uow.session.get_by_id(session_id)
                            if session:
                                model_id = session.model_id
                    if not model_id:
                        default = await self._runner_factory._llm_model_service.get_default_model()
                        model_id = default.id if default else None
                    if model_id and await get_llm_circuit_breaker().is_open(model_id):
                        logger.info("DLQ 重放暂停（模型熔断开路）: model_id=%s", model_id)
                        break
                    await self._task_state.replay_dlq_entry(message_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("DLQ 重放循环异常: %s", exc)

    async def _reconcile_orphaned_tasks(self, reason: str) -> None:
        admission = get_admission_runtime_settings()
        stale_after = max(120.0, admission.task_execution_lease_seconds * 2.0)
        try:
            async with get_uow() as uow:
                sessions = await uow.session.list_recoverable_running(limit=100)
        except Exception as exc:
            logger.warning("任务恢复对账查询失败 reason=%s: %s", reason, exc)
            return

        for session in sessions:
            task_id = session.task_id
            if not task_id:
                continue
            try:
                snapshot = await self._task_state.get_runtime_snapshot(task_id)
                if snapshot.get("is_done") or not self._task_state.heartbeat_is_stale(
                    snapshot.get("meta"),
                    stale_after,
                ):
                    continue
                lease_owner = await get_task_lease_owner(task_id)
                if lease_owner:
                    continue

                model_id = session.model_id
                if not model_id:
                    default_model = await self._runner_factory._llm_model_service.get_default_model()
                    model_id = default_model.id if default_model else None
                if model_id and await get_llm_circuit_breaker().is_open(model_id):
                    logger.info(
                        "孤儿任务跳过恢复（模型熔断开路）: session_id=%s task_id=%s model_id=%s reason=%s",
                        session.id,
                        task_id,
                        model_id,
                        reason,
                    )
                    continue

                checkpoint = await self._checkpoint_service.resume_latest_checkpoint(session.id)
                task = self._task_cls.from_task_id(
                    task_id,
                    session.id,
                    self._task_state,
                )
                if not checkpoint or not await requeue_latest_user_message(
                        task,
                        session.id,
                        get_uow,
                ):
                    logger.warning(
                        "孤儿任务无可恢复输入，标记失败: session_id=%s task_id=%s",
                        session.id,
                        task_id,
                    )
                    await self._task_state.set_status(task_id, TaskStatus.FAILED)
                    async with get_uow() as uow:
                        await uow.session.update_status(session.id, SessionStatus.FAILED)
                    continue

                await self._task_state.clear_cancel(task_id)
                await self._task_state.set_status(task_id, TaskStatus.PENDING)
                await self._task_state.dispatch(task_id, session.id)
                logger.warning(
                    "孤儿任务已从 checkpoint 恢复并重新派发: session_id=%s task_id=%s checkpoint_id=%s reason=%s",
                    session.id,
                    task_id,
                    checkpoint.id,
                    reason,
                )
            except Exception as exc:
                logger.exception(
                    "任务恢复对账失败: session_id=%s task_id=%s error=%s",
                    session.id,
                    task_id,
                    exc,
                )

    async def _handle_claimed_job(
            self,
            message_id: str,
            task_id: str,
            session_id: str,
    ) -> None:
        meta = await self._task_state.get_task_meta(task_id) or {}
        request_id = meta.get("request_id") or ""
        with bind_context(
            session_id=session_id,
            task_id=task_id,
            worker_id=get_worker_id(),
            request_id=request_id or None,
        ):
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
            await self._task_state.record_heartbeat(task_id, get_worker_id())
            try:
                await self._execute_job_with_lease_renewal(
                    task_id,
                    session_id,
                    admission.task_execution_lease_seconds,
                )
                await self._task_state.ack_dispatch(message_id)
            except ModelUnavailableError as exc:
                logger.warning(
                    "Worker 模型快速失败: task_id=%s session_id=%s code=%s error=%s",
                    task_id,
                    session_id,
                    exc.error_code,
                    exc,
                )
                async with get_uow() as uow:
                    await uow.session.update_status(session_id, SessionStatus.FAILED)
                await self._task_state.mark_dispatch_failure(
                    message_id=message_id,
                    task_id=task_id,
                    session_id=session_id,
                    error=str(exc),
                    error_code=exc.error_code,
                    fast_fail=True,
                )
            except Exception as exc:
                logger.exception(
                    "Worker 执行任务失败: task_id=%s session_id=%s error=%s",
                    task_id,
                    session_id,
                    exc,
                )
                error_code = classify_llm_error_code(exc)
                await prepare_recoverable_retry(
                    session_id=session_id,
                    task_id=task_id,
                    task_cls=self._task_cls,
                    uow_factory=get_uow,
                    checkpoint_service=self._checkpoint_service,
                    error_code=error_code,
                )
                await self._task_state.mark_dispatch_failure(
                    message_id=message_id,
                    task_id=task_id,
                    session_id=session_id,
                    error=str(exc),
                    error_code=error_code,
                )
            finally:
                await release_task_lease(task_id)

    async def _execute_job_with_lease_renewal(
            self,
            task_id: str,
            session_id: str,
            lease_ttl_seconds: int,
    ) -> None:
        execution = asyncio.create_task(self._execute_job(task_id, session_id))
        lease_lost = asyncio.Event()

        async def lease_renewer() -> None:
            interval = max(5.0, lease_ttl_seconds / 3)
            while not execution.done():
                await asyncio.sleep(interval)
                if execution.done():
                    return
                if not await renew_task_lease(task_id, lease_ttl_seconds):
                    logger.warning(
                        "任务执行租约续期失败，停止当前执行: task_id=%s session_id=%s",
                        task_id,
                        session_id,
                    )
                    lease_lost.set()
                    return
                await self._task_state.record_heartbeat(task_id, get_worker_id())

        renewal = asyncio.create_task(lease_renewer())
        try:
            done, _ = await asyncio.wait(
                {execution, renewal},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal in done and lease_lost.is_set() and not execution.done():
                execution.cancel()
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
                raise RuntimeError("任务执行租约续期失败")
            await execution
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass

    async def _execute_job(self, task_id: str, session_id: str) -> None:
        meta = await self._task_state.get_task_meta(task_id) or {}
        if meta.get("status") == TaskStatus.PENDING.value:
            await self._task_state.clear_cancel(task_id)

        if await self._task_state.is_cancelled(task_id):
            await self._task_state.set_status(task_id, TaskStatus.CANCELLED)
            return

        if meta.get("task_type") == "codebase_ingest":
            await self._execute_ingest_job(task_id, meta.get("resource_id", ""))
            return
        if meta.get("task_type") == "kb_ingest":
            await self._execute_kb_ingest_job(task_id, meta.get("resource_id", ""))
            return

        async with get_uow() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            logger.error("Worker 找不到会话: session_id=%s task_id=%s", session_id, task_id)
            await self._task_state.set_status(task_id, TaskStatus.FAILED)
            raise RuntimeError(f"任务会话不存在: {session_id}")
        if session.status in {SessionStatus.CANCELLED, SessionStatus.FAILED}:
            meta = await self._task_state.get_task_meta(task_id) or {}
            allow_failed_retry = (
                session.status == SessionStatus.FAILED
                and meta.get("status") == TaskStatus.PENDING.value
                and int(meta.get("retry_count") or 0) > 0
            )
            if not allow_failed_retry:
                logger.info(
                    "Worker 跳过终态会话: session_id=%s task_id=%s status=%s",
                    session_id,
                    task_id,
                    session.status.value,
                )
                await self._task_state.set_status(
                    task_id,
                    TaskStatus.CANCELLED if session.status == SessionStatus.CANCELLED else TaskStatus.FAILED,
                )
                return
            async with get_uow() as uow:
                await uow.session.update_status(session_id, SessionStatus.RUNNING)

        async with get_uow() as uow:
            await uow.session.update_status(session_id, SessionStatus.RUNNING)

        model_id = session.model_id
        if not model_id:
            default_model = await self._runner_factory._llm_model_service.get_default_model()
            model_id = default_model.id if default_model else None
        runtime = get_runtime_config()
        if (
            model_id
            and runtime.model_resilience.fast_fail_on_open_circuit
            and await get_llm_circuit_breaker().is_open(model_id)
        ):
            raise ModelUnavailableError(
                "模型熔断开路，任务快速失败",
                error_code=MODEL_UNAVAILABLE,
            )

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

    async def _execute_kb_ingest_job(self, task_id: str, kb_id: str) -> None:
        if not kb_id:
            await self._task_state.set_status(task_id, TaskStatus.FAILED)
            raise RuntimeError("知识库摄取任务缺少 resource_id")
        llm = None
        try:
            model = await self._runner_factory._llm_model_service.resolve_model(None)
            llm = create_resilient_llm(
                model,
                thinking_enabled=False,
                llm_model_service=self._runner_factory._llm_model_service,
            )
        except Exception as exc:
            logger.warning("知识库摄取 GraphRAG LLM 不可用，将跳过建图: %s", exc)
        container = get_worker_container()
        runner = KBIngestionTaskRunner(
            uow_factory=get_uow,
            file_storage=self._file_storage,
            kb_id=kb_id,
            llm=llm,
            json_parser=container.json_parser(),
        )
        task = self._task_cls(
            task_id=task_id,
            session_id=f"kb-ingest:{kb_id}",
            task_runner=runner,
        )
        await task.execute_locally()

    def request_shutdown(self) -> None:
        self._running = False


async def main() -> None:
    setup_logging()
    configure_structured_logging()
    container = await init_worker_container()
    from app.application.services.skill_service import SkillService

    await bootstrap_data(
        uow_factory=get_uow,
        skill_service=SkillService(uow_factory=get_uow),
    )

    await _startup_reconcile()
    dlq_count = await get_task_state().count_dlq_messages()
    if dlq_count:
        logger.warning("Worker 启动检测到 DLQ 积压: count=%s", dlq_count)
    sandbox_cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())
    from app.infrastructure.external.scheduler.job_scheduler import run_scheduler_loop
    from app.application.services.scheduled_job_service import ScheduledJobService
    from app.application.services.notification_service import NotificationService

    notification_service = NotificationService(uow_factory=get_uow)
    app_config = await container.app_config_provider().get()
    scheduler_stop = asyncio.Event()
    scheduler_task = asyncio.create_task(
        run_scheduler_loop(
            get_uow,
            ScheduledJobService(uow_factory=get_uow),
            notification_service=notification_service,
            mcp_pool=container.mcp_connection_pool(),
            app_config=app_config,
            stop_event=scheduler_stop,
        )
    )
    from app.application.services.audit_service import AuditService
    from app.application.services.takeover_timeout_sweep import run_takeover_timeout_loop

    takeover_stop = asyncio.Event()
    takeover_task = asyncio.create_task(
        run_takeover_timeout_loop(
            get_uow,
            AuditService(uow_factory=get_uow),
            stop_event=takeover_stop,
        )
    )
    worker = AgentWorker(
        runner_factory=await container.task_runner_factory(),
        checkpoint_service=await container.checkpoint_service(),
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
        scheduler_stop.set()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        takeover_stop.set()
        takeover_task.cancel()
        try:
            await takeover_task
        except asyncio.CancelledError:
            pass
        sandbox_cleanup_task.cancel()
        try:
            await sandbox_cleanup_task
        except asyncio.CancelledError:
            pass
        await RedisStreamTask.destroy()
        await shutdown_worker_container(container)


if __name__ == "__main__":
    asyncio.run(main())
