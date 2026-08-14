#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone agent worker: consumes task dispatch queue and runs AgentTaskRunner."""
import asyncio
import logging
import signal
import socket
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.application.services.bootstrap_service import bootstrap_data
from app.application.services.config_provider import get_runtime_config
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.container import get_worker_container, init_worker_container, shutdown_worker_container
from app.domain.external.sandbox import Sandbox
from app.domain.external.task import RecoverableTaskReconciliationRequired
from app.domain.models.event import MessageEvent
from app.domain.models.session import SessionStatus
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from app.domain.services.knowledge_base.ingest_errors import NonRecoverableIngestError
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
)
from app.domain.services.knowledge_base.ingestion_task_runner import KBIngestionTaskRunner
from app.domain.external.file_storage import FileStorage
from app.infrastructure.external.runtime_settings import get_admission_runtime_settings
from app.infrastructure.external.sandbox.admission import get_sandbox_quota
from app.infrastructure.external.sandbox.sandbox_maintenance import run_sandbox_maintenance
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_lease import (
    TaskLeaseAcquireResult,
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
from app.domain.models.knowledge_base import KBStatus
from app.domain.models.resource_governance import (
    BuildState,
    ResourceKind,
)
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

_KB_INGEST_SESSION_PREFIX = "kb-ingest:"


async def _finalize_kb_ingest_failure(kb_id: str, error: str) -> None:
    """Ensure KB reaches a terminal failed state and clear stale ingest_task_id."""
    non_terminal = {
        KBStatus.PENDING,
        KBStatus.PARSING,
        KBStatus.CHUNKING,
        KBStatus.INDEXING,
        KBStatus.GRAPH_BUILDING,
    }
    async with get_uow() as uow:
        kb = await uow.knowledge_base.get_kb(kb_id)
        if not kb:
            return
        if kb.status in non_terminal:
            await uow.knowledge_base.update_status(kb_id, KBStatus.FAILED, error)
        kb = await uow.knowledge_base.get_kb(kb_id)
        if kb and kb.status == KBStatus.FAILED:
            kb.ingest_task_id = None
            await uow.knowledge_base.save_kb(kb)


def _kb_id_from_ingest_session(session_id: str) -> str | None:
    if not session_id.startswith(_KB_INGEST_SESSION_PREFIX):
        return None
    kb_id = session_id[len(_KB_INGEST_SESSION_PREFIX):]
    return kb_id or None

SHUTDOWN_GRACE_SECONDS = 30
TASK_RECONCILE_INTERVAL_SECONDS = 30


class DispatchClaimDecision(str, Enum):
    ACK_DUPLICATE = "ACK_DUPLICATE"
    EXECUTE = "EXECUTE"
    REQUEUE = "REQUEUE"


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
        await self._reconcile_stale_kb_builds("startup")
        await self._reconcile_stale_codebase_builds("startup")
        await self._reconcile_stuck_kb_ingests("startup")
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
                    message_id, task_id, session_id, run_generation = claim
                    task = asyncio.create_task(
                        self._handle_claimed_job(
                            message_id,
                            task_id,
                            session_id,
                            run_generation,
                        ),
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
            await self._reconcile_stale_kb_builds("periodic")
            await self._reconcile_stale_codebase_builds("periodic")
            await self._reconcile_stuck_kb_ingests("periodic")

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
                run_generation = int(snapshot.get("run_generation") or 1)
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
                    run_generation,
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
                    await self._task_state.set_status(
                        task_id,
                        run_generation,
                        TaskStatus.FAILED,
                    )
                    async with get_uow() as uow:
                        await uow.session.update_status(session.id, SessionStatus.FAILED)
                    continue

                await self._task_state.clear_cancel(task_id)
                replacement_generation = run_generation + 1
                replacement_message_id = await self._task_state.dispatch(
                    task_id,
                    session.id,
                    replacement_generation,
                )
                next_generation = await self._task_state.begin_recovery_attempt(
                    task_id,
                    run_generation,
                    durable_dispatch_message_id=replacement_message_id,
                )
                if next_generation is None:
                    continue
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

    async def _reconcile_stale_kb_builds(self, reason: str) -> None:
        """Terminalize stale shared builds without touching the active pin."""
        admission = get_admission_runtime_settings()
        stale_seconds = max(
            120.0,
            admission.task_execution_lease_seconds * 2.0,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=stale_seconds
        )
        try:
            async with get_uow() as uow:
                builds = await uow.resource_governance.list_stale_builds(
                    ResourceKind.KNOWLEDGE_BASE,
                    stale_before=cutoff,
                    limit=100,
                )
        except Exception as exc:
            logger.warning(
                "共享知识库构建对账查询失败 reason=%s: %s",
                reason,
                exc,
            )
            return

        build_service = get_worker_container().resource_build_service()
        runner = KBIngestionRunner(
            uow_factory=get_uow,
            file_storage=getattr(self, "_file_storage", None),
            build_service=build_service,
        )
        for build in builds:
            try:
                lease_owner = await get_task_lease_owner(build.id)
                if lease_owner:
                    continue
                error = "knowledge-base build heartbeat expired"
                await runner.reconcile_stale(build.id, error=error)
                logger.warning(
                    "共享知识库构建已对账: build=%s kb=%s reason=%s",
                    build.id,
                    build.resource_id,
                    reason,
                )
            except Exception as exc:
                logger.exception(
                    "共享知识库构建对账失败 build=%s: %s",
                    build.id,
                    exc,
                )

    async def _reconcile_stale_codebase_builds(self, reason: str) -> None:
        """Terminalize stale codebase candidate builds without touching the active pin."""
        admission = get_admission_runtime_settings()
        stale_seconds = max(
            120.0,
            admission.task_execution_lease_seconds * 2.0,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=stale_seconds
        )
        try:
            async with get_uow() as uow:
                builds = await uow.resource_governance.list_stale_builds(
                    ResourceKind.CODEBASE,
                    stale_before=cutoff,
                    limit=100,
                )
        except Exception as exc:
            logger.warning(
                "代码库候选构建对账查询失败 reason=%s: %s",
                reason,
                exc,
            )
            return

        runner = CodebaseIngestionRunner(
            uow_factory=get_uow,
            sandbox_cls=getattr(self, "_sandbox_cls", None),
            file_storage=getattr(self, "_file_storage", None),
        )
        for build in builds:
            try:
                lease_owner = await get_task_lease_owner(build.id)
                if lease_owner:
                    continue
                error = "codebase build heartbeat expired"
                await runner.reconcile_stale(build.id, error=error)
                logger.warning(
                    "代码库候选构建已对账: build=%s codebase=%s reason=%s",
                    build.id,
                    build.resource_id,
                    reason,
                )
            except Exception as exc:
                logger.exception(
                    "代码库候选构建对账失败 build=%s: %s",
                    build.id,
                    exc,
                )

    async def _reconcile_stuck_kb_ingests(self, reason: str) -> None:
        """Compatibility-only reconciliation for pre-versioned tasks."""
        admission = get_admission_runtime_settings()
        stale_after = max(120.0, admission.task_execution_lease_seconds * 2.0)
        try:
            async with get_uow() as uow:
                kbs = await uow.knowledge_base.list_stuck_ingesting(limit=100)
        except Exception as exc:
            logger.warning("知识库摄取对账查询失败 reason=%s: %s", reason, exc)
            return

        for kb in kbs:
            task_id = kb.ingest_task_id
            if not task_id:
                continue
            try:
                async with get_uow() as uow:
                    shared_build = (
                        await uow.resource_governance.get_build(task_id)
                    )
                if shared_build is not None:
                    # PostgreSQL ResourceBuild is authoritative for all new
                    # candidates; Redis metadata must not terminalize it.
                    continue
                snapshot = await self._task_state.get_runtime_snapshot(task_id)
                run_generation = int(snapshot.get("run_generation") or 1)
                if snapshot.get("is_done"):
                    await _finalize_kb_ingest_failure(
                        kb.id,
                        kb.error or "知识库索引任务已终止",
                    )
                    continue
                if not self._task_state.heartbeat_is_stale(snapshot.get("meta"), stale_after):
                    continue
                lease_owner = await get_task_lease_owner(task_id)
                if lease_owner:
                    continue
                await self._task_state.set_status(
                    task_id,
                    run_generation,
                    TaskStatus.FAILED,
                )
                await _finalize_kb_ingest_failure(
                    kb.id,
                    kb.error or "知识库索引任务超时或 worker 异常退出",
                )
                logger.warning(
                    "孤儿知识库摄取任务已清理: kb_id=%s task_id=%s reason=%s",
                    kb.id,
                    task_id,
                    reason,
                )
            except Exception as exc:
                logger.exception(
                    "知识库摄取对账失败: kb_id=%s task_id=%s error=%s",
                    kb.id,
                    task_id,
                    exc,
                )

    async def _handle_claimed_job(
            self,
            message_id: str,
            task_id: str,
            session_id: str,
            run_generation: int,
    ) -> DispatchClaimDecision:
        meta = await self._task_state.get_task_meta(task_id) or {}
        request_id = meta.get("request_id") or ""
        with bind_context(
            session_id=session_id,
            task_id=task_id,
            worker_id=get_worker_id(),
            request_id=request_id or None,
        ):
            admission = get_admission_runtime_settings()
            if not meta:
                return DispatchClaimDecision.REQUEUE
            current_generation = int(meta.get("run_generation", 1))
            if run_generation < current_generation:
                if await self._task_state.can_ack_stale_dispatch(
                    task_id,
                    run_generation,
                ):
                    await self._task_state.ack_dispatch(message_id)
                    return DispatchClaimDecision.ACK_DUPLICATE
                return DispatchClaimDecision.REQUEUE
            if run_generation > current_generation:
                return DispatchClaimDecision.REQUEUE
            if meta.get("status") in {
                TaskStatus.DONE.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.FAILED.value,
            }:
                await self._task_state.ack_dispatch(message_id)
                return DispatchClaimDecision.ACK_DUPLICATE
            lease_result = await try_acquire_task_lease(
                task_id,
                run_generation,
                admission.task_execution_lease_seconds,
            )
            if lease_result == TaskLeaseAcquireResult.STALE_GENERATION:
                if not await self._task_state.can_ack_stale_dispatch(
                    task_id,
                    run_generation,
                ):
                    return DispatchClaimDecision.REQUEUE
                await self._task_state.ack_dispatch(message_id)
                return DispatchClaimDecision.ACK_DUPLICATE
            if lease_result in {
                TaskLeaseAcquireResult.TERMINAL,
                TaskLeaseAcquireResult.SAME_GENERATION_CONFLICT,
            }:
                await self._task_state.ack_dispatch(message_id)
                if (
                    lease_result
                    == TaskLeaseAcquireResult.SAME_GENERATION_CONFLICT
                ):
                    logger.warning(
                        "任务执行租约冲突，跳过重复执行: task_id=%s session_id=%s",
                        task_id,
                        session_id,
                    )
                return DispatchClaimDecision.ACK_DUPLICATE
            if lease_result != TaskLeaseAcquireResult.ACQUIRED:
                logger.warning(
                    "任务执行租约暂不可用，保留派发: "
                    "task_id=%s session_id=%s classification=%s",
                    task_id,
                    session_id,
                    lease_result.value,
                )
                return DispatchClaimDecision.REQUEUE
            heartbeat_changed = await self._task_state.record_heartbeat(
                task_id,
                run_generation,
                get_worker_id(),
            )
            if not heartbeat_changed:
                await release_task_lease(task_id, run_generation)
                if await self._task_state.can_ack_stale_dispatch(
                    task_id,
                    run_generation,
                ):
                    await self._task_state.ack_dispatch(message_id)
                    return DispatchClaimDecision.ACK_DUPLICATE
                return DispatchClaimDecision.REQUEUE
            try:
                await self._execute_job_with_lease_renewal(
                    task_id,
                    session_id,
                    run_generation,
                    admission.task_execution_lease_seconds,
                )
                await self._task_state.ack_dispatch(message_id)
                return DispatchClaimDecision.EXECUTE
            except RecoverableTaskReconciliationRequired as exc:
                logger.warning(
                    "Worker 保留待对账派发，等待重新认领: "
                    "task_id=%s session_id=%s error=%s",
                    task_id,
                    session_id,
                    exc,
                )
                return DispatchClaimDecision.REQUEUE
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
                    run_generation=run_generation,
                    error=str(exc),
                    error_code=exc.error_code,
                    fast_fail=True,
                )
                return DispatchClaimDecision.EXECUTE
            except NonRecoverableIngestError as exc:
                logger.error(
                    "Worker 知识库摄取不可恢复失败: task_id=%s session_id=%s error=%s",
                    task_id,
                    session_id,
                    exc,
                )
                kb_id = _kb_id_from_ingest_session(session_id)
                if kb_id:
                    await _finalize_kb_ingest_failure(kb_id, str(exc))
                await self._task_state.mark_dispatch_failure(
                    message_id=message_id,
                    task_id=task_id,
                    session_id=session_id,
                    run_generation=run_generation,
                    error=str(exc),
                    error_code=exc.error_code,
                    fast_fail=True,
                )
                return DispatchClaimDecision.EXECUTE
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
                    run_generation=run_generation,
                    error=str(exc),
                    error_code=error_code,
                )
                kb_id = _kb_id_from_ingest_session(session_id)
                if kb_id:
                    meta = await self._task_state.get_task_meta(task_id) or {}
                    if meta.get("status") == TaskStatus.FAILED.value:
                        await _finalize_kb_ingest_failure(kb_id, str(exc))
                return DispatchClaimDecision.EXECUTE
            finally:
                await release_task_lease(task_id, run_generation)

    async def _execute_job_with_lease_renewal(
            self,
            task_id: str,
            session_id: str,
            run_generation: int,
            lease_ttl_seconds: int,
    ) -> None:
        execution = asyncio.create_task(
            self._execute_job(task_id, session_id, run_generation)
        )
        lease_lost = asyncio.Event()

        async def lease_renewer() -> None:
            interval = max(5.0, lease_ttl_seconds / 3)
            while not execution.done():
                await asyncio.sleep(interval)
                if execution.done():
                    return
                if not await renew_task_lease(
                    task_id,
                    run_generation,
                    lease_ttl_seconds,
                ):
                    logger.warning(
                        "任务执行租约续期失败，停止当前执行: task_id=%s session_id=%s",
                        task_id,
                        session_id,
                    )
                    lease_lost.set()
                    return
                changed = await self._task_state.record_heartbeat(
                    task_id,
                    run_generation,
                    get_worker_id(),
                )
                if not changed:
                    lease_lost.set()
                    return

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

    async def _execute_job(
            self,
            task_id: str,
            session_id: str,
            run_generation: int,
    ) -> None:
        meta = await self._task_state.get_task_meta(task_id) or {}
        if meta.get("status") == TaskStatus.PENDING.value:
            await self._task_state.clear_cancel(task_id)

        if await self._task_state.is_cancelled(task_id):
            if meta.get("task_type") == "kb_ingest":
                container = get_worker_container()
                await KBIngestionRunner(
                    uow_factory=get_uow,
                    file_storage=self._file_storage,
                    build_service=container.resource_build_service(),
                ).cancel(task_id)
            await self._task_state.set_status(
                task_id,
                run_generation,
                TaskStatus.CANCELLED,
            )
            return

        if meta.get("task_type") == "codebase_ingest":
            await self._execute_ingest_job(
                task_id,
                meta.get("resource_id", ""),
                run_generation,
            )
            return
        if meta.get("task_type") == "kb_ingest":
            await self._execute_kb_ingest_job(
                task_id,
                meta.get("resource_id", ""),
                run_generation,
            )
            return

        async with get_uow() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            logger.error("Worker 找不到会话: session_id=%s task_id=%s", session_id, task_id)
            await self._task_state.set_status(
                task_id,
                run_generation,
                TaskStatus.FAILED,
            )
            raise RuntimeError(f"任务会话不存在: {session_id}")
        has_run_reconciliation = isinstance(
            meta.get("run_reconciliation"),
            dict,
        )
        if (
            session.status in {
                SessionStatus.CANCELLED,
                SessionStatus.FAILED,
            }
            and not has_run_reconciliation
        ):
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
                    run_generation,
                    TaskStatus.CANCELLED if session.status == SessionStatus.CANCELLED else TaskStatus.FAILED,
                )
                return
        if not has_run_reconciliation:
            async with get_uow() as uow:
                await uow.session.update_status(
                    session_id,
                    SessionStatus.RUNNING,
                )

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
            run_generation=run_generation,
            task_runner=runner,
            task_state=self._task_state,
        )

        async def cancel_watcher() -> None:
            while not await task.is_done():
                if await self._task_state.wait_for_cancel(task_id, timeout_seconds=5.0):
                    task.cancel()
                    break

        watcher = asyncio.create_task(cancel_watcher())
        try:
            await task.execute_locally()
            recoverable_error = getattr(
                task,
                "recoverable_error",
                None,
            )
            if isinstance(recoverable_error, BaseException):
                raise recoverable_error
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

    async def _execute_ingest_job(
            self,
            task_id: str,
            codebase_id: str,
            run_generation: int,
    ) -> None:
        if not codebase_id:
            await self._task_state.set_status(
                task_id,
                run_generation,
                TaskStatus.FAILED,
            )
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
            run_generation=run_generation,
            task_runner=runner,
            task_state=self._task_state,
        )
        await task.execute_locally()

    async def _execute_kb_ingest_job(
            self,
            task_id: str,
            kb_id: str,
            run_generation: int,
    ) -> None:
        if not kb_id:
            await self._task_state.set_status(
                task_id,
                run_generation,
                TaskStatus.FAILED,
            )
            raise RuntimeError("知识库摄取任务缺少 resource_id")
        llm = None
        ocr_llm = None
        try:
            model = await self._runner_factory._llm_model_service.resolve_model(None)
            llm = create_resilient_llm(
                model,
                thinking_enabled=False,
                llm_model_service=self._runner_factory._llm_model_service,
            )
        except Exception as exc:
            logger.warning("知识库摄取 GraphRAG LLM 不可用，将跳过建图: %s", exc)
        try:
            vision_model = await self._runner_factory._llm_model_service.resolve_vision_model()
            if vision_model:
                ocr_llm = create_resilient_llm(
                    vision_model,
                    thinking_enabled=False,
                    llm_model_service=self._runner_factory._llm_model_service,
                )
            else:
                logger.warning("无可用视觉模型，图片型 PDF OCR 将不可用")
        except Exception as exc:
            logger.warning("知识库摄取 OCR LLM 不可用: %s", exc)
        container = get_worker_container()
        runner = KBIngestionTaskRunner(
            uow_factory=get_uow,
            file_storage=self._file_storage,
            build_id=task_id,
            llm=llm,
            ocr_llm=ocr_llm,
            json_parser=container.json_parser(),
            build_service=container.resource_build_service(),
        )
        task = self._task_cls(
            task_id=task_id,
            session_id=f"kb-ingest:{kb_id}",
            run_generation=run_generation,
            task_runner=runner,
            task_state=self._task_state,
        )
        await task.execute_locally()

    def request_shutdown(self) -> None:
        self._running = False


async def main() -> None:
    from app.application.security.authorization_context import set_authorization_context
    from app.domain.models.authorization import AuthorizationContext

    set_authorization_context(AuthorizationContext.system("worker"))
    setup_logging()
    configure_structured_logging()
    container = await init_worker_container()

    settings = get_settings()
    if settings.worker_metrics_port:
        from prometheus_client import start_http_server

        start_http_server(settings.worker_metrics_port)
        logger.info(
            "Worker Prometheus metrics server listening on :%s",
            settings.worker_metrics_port,
        )

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
    from app.application.services.patrol_retention_service import PatrolRetentionService
    from app.application.services.resource_version_gc_service import (
        ResourceVersionGCService,
    )

    notification_service = NotificationService(uow_factory=get_uow)
    app_config = await container.app_config_provider().get()
    scheduler_stop = asyncio.Event()
    scheduler_task = asyncio.create_task(
        run_scheduler_loop(
            get_uow,
            ScheduledJobService(
                uow_factory=get_uow,
                patrol_run_service=await container.patrol_run_service(),
            ),
            notification_service=notification_service,
            mcp_pool=container.mcp_connection_pool(),
            app_config=app_config,
            resource_version_gc_service=ResourceVersionGCService(
                uow_factory=get_uow,
                object_storage=container.object_storage(),
            ),
            patrol_retention_service=PatrolRetentionService(uow_factory=get_uow),
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
