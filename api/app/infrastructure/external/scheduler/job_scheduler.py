import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

from app.application.ports.coordination import LeaseManagerPort
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import (
    RuntimePolicyIntegrityError,
    RuntimePolicyStaleError,
    RuntimePolicyUnavailableError,
)
from app.domain.utils.time_utils import utc_now

if TYPE_CHECKING:
    from app.application.services.patrol_retention_service import (
        PatrolRetentionService,
    )
    from app.application.services.resource_version_gc_service import (
        ResourceVersionGCService,
    )
    from app.domain.external.connection_pool import (
        A2AConnectionPoolPort,
        MCPConnectionPoolPort,
    )

logger = logging.getLogger(__name__)

SCHEDULER_LEADER_KEY = "scheduler:leader"
KNOWLEDGE_VERSION_GC_LEASE_KEY = "scheduler:knowledge-version-gc"
PATROL_RETENTION_LEASE_KEY = "scheduler:patrol-retention"


async def try_become_scheduler_leader(
    leases: LeaseManagerPort,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    """Atomically acquire or token-check renewal of the worker leader lease."""
    if await leases.acquire(
        SCHEDULER_LEADER_KEY,
        worker_id,
        ttl_seconds=lease_seconds,
    ):
        return True
    return await leases.renew(
        SCHEDULER_LEADER_KEY,
        worker_id,
        ttl_seconds=lease_seconds,
    )


async def _keep_scheduler_lease_alive(
    leases: LeaseManagerPort,
    key: str,
    owner_token: str,
    lease_seconds: float,
) -> None:
    interval = max(0.01, lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        if not await leases.renew(
            key,
            owner_token,
            ttl_seconds=lease_seconds,
        ):
            raise RuntimeError(f"scheduler lease lost: {key}")


async def run_under_renewed_lease(
    work: Coroutine[Any, Any, Any],
    *,
    leases: LeaseManagerPort,
    key: str,
    owner_token: str,
    lease_seconds: float,
    lost_message: str,
) -> Any:
    """Run ``work`` while continuously renewing ``key`` under ``owner_token``.

    The lease is renewed on a background keepalive (``lease_seconds / 3`` cadence)
    for the *entire* duration of ``work``, so a tick that outlives one lease TTL
    no longer lets a peer replica acquire the same lease and double-execute
    (P1-4 双主). ``work`` is raced against the keepalive: if the lease is lost
    mid-flight the keepalive raises, ``work`` is cancelled, and the lost-lease
    error propagates so the caller can abandon the tick instead of continuing
    without leadership. The caller owns acquire/release of ``key``.
    """
    collection = asyncio.create_task(work)
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
            leases,
            key,
            owner_token,
            lease_seconds,
        )
    )
    try:
        done, _pending = await asyncio.wait(
            {collection, keepalive},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if collection in done:
            return await collection
        collection.cancel()
        await asyncio.gather(collection, return_exceptions=True)
        await keepalive
        raise RuntimeError(lost_message)
    finally:
        if not collection.done():
            collection.cancel()
            await asyncio.gather(collection, return_exceptions=True)
        keepalive.cancel()
        await asyncio.gather(keepalive, return_exceptions=True)


async def run_knowledge_version_gc_tick(
    service: "ResourceVersionGCService",
    *,
    leases: LeaseManagerPort,
    worker_id: str,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one GC transaction inside a token-owned, renewed cluster lease."""
    token = owner_token or f"{worker_id}:{uuid.uuid4().hex}"
    if not await leases.acquire(
        KNOWLEDGE_VERSION_GC_LEASE_KEY,
        token,
        ttl_seconds=lease_seconds,
    ):
        return None
    try:
        return await run_under_renewed_lease(
            service.collect_knowledge_versions(),
            leases=leases,
            key=KNOWLEDGE_VERSION_GC_LEASE_KEY,
            owner_token=token,
            lease_seconds=lease_seconds,
            lost_message="knowledge-version GC lease ended unexpectedly",
        )
    finally:
        await leases.release(
            KNOWLEDGE_VERSION_GC_LEASE_KEY,
            token,
        )


async def run_patrol_retention_tick(
    service: "PatrolRetentionService",
    *,
    leases: LeaseManagerPort,
    worker_id: str,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one Patrol retention batch under an independently renewed lease."""
    token = owner_token or f"{worker_id}:{uuid.uuid4().hex}"
    if not await leases.acquire(
        PATROL_RETENTION_LEASE_KEY,
        token,
        ttl_seconds=lease_seconds,
    ):
        return None
    try:
        return await run_under_renewed_lease(
            service.cleanup(),
            leases=leases,
            key=PATROL_RETENTION_LEASE_KEY,
            owner_token=token,
            lease_seconds=lease_seconds,
            lost_message="Patrol retention lease ended unexpectedly",
        )
    finally:
        await leases.release(PATROL_RETENTION_LEASE_KEY, token)


async def run_scheduler_loop(
    uow_factory: Callable[[], IUnitOfWork],
    job_service: ScheduledJobService,
    *,
    leases: LeaseManagerPort,
    worker_id: str,
    policy_reader: OperationsPolicyReader,
    stop_event: asyncio.Event,
    resource_version_gc_service: Optional["ResourceVersionGCService"] = None,
    patrol_retention_service: Optional["PatrolRetentionService"] = None,
    mcp_pool: Optional["MCPConnectionPoolPort"] = None,
    a2a_pool: Optional["A2AConnectionPoolPort"] = None,
) -> None:
    """Execution-kernel scheduler loop: poll definitions and admit Runs."""
    while not stop_event.is_set():
        now = utc_now()
        try:
            active = await policy_reader.active_operations(
                require_fresh=True,
                now=now,
            )
        except (
            RuntimePolicyIntegrityError,
            RuntimePolicyStaleError,
            RuntimePolicyUnavailableError,
        ) as exc:
            logger.error("Scheduler denied by Runtime Policy state: %s", exc.error_key)
            try:
                last_verified = await policy_reader.active_operations(
                    require_fresh=False,
                    now=now,
                )
                retry_seconds = last_verified.revision.policy.scheduler.poll_interval_seconds
            except (
                RuntimePolicyIntegrityError,
                RuntimePolicyStaleError,
                RuntimePolicyUnavailableError,
            ):
                retry_seconds = 1.0
            await _wait_or_stop(stop_event, retry_seconds)
            continue
        operations = active.revision.policy
        sched_cfg = operations.scheduler
        if not sched_cfg.enabled:
            await _wait_or_stop(stop_event, sched_cfg.poll_interval_seconds)
            continue

        # Recycle idle MCP/A2A connections (and their stdio subprocesses) that
        # outlived their config -- e.g. after an integration was disabled or
        # deleted. Process-local, so every worker prunes its own pool.
        if mcp_pool is not None:
            try:
                await mcp_pool.release_stale()
            except (OSError, RuntimeError) as exc:
                logger.warning("MCP pool release_stale failed: %s", exc)
        if a2a_pool is not None:
            try:
                await a2a_pool.release_stale()
            except (OSError, RuntimeError) as exc:
                logger.warning("A2A pool release_stale failed: %s", exc)

        if not await try_become_scheduler_leader(
            leases,
            worker_id=worker_id,
            lease_seconds=sched_cfg.leader_lease_seconds,
        ):
            await _wait_or_stop(stop_event, sched_cfg.poll_interval_seconds)
            continue

        try:
            # Keep SCHEDULER_LEADER_KEY renewed for the *entire* leader tick so a
            # tick that outlives one lease TTL cannot let a peer replica acquire
            # the key and double-trigger jobs (P1-4 双主).
            await run_under_renewed_lease(
                _run_scheduler_leader_tick(
                    job_service=job_service,
                    uow_factory=uow_factory,
                    leases=leases,
                    worker_id=worker_id,
                    sched_cfg=sched_cfg,
                    operations=operations,
                    resource_version_gc_service=resource_version_gc_service,
                    patrol_retention_service=patrol_retention_service,
                ),
                leases=leases,
                key=SCHEDULER_LEADER_KEY,
                owner_token=worker_id,
                lease_seconds=sched_cfg.leader_lease_seconds,
                lost_message="scheduler leader lease ended mid-tick",
            )
        except RuntimeError as exc:
            # Lease lost mid-tick: abandon this tick without leadership rather
            # than continuing to trigger jobs a peer replica may now own.
            logger.warning("Scheduler leader tick aborted: %s", exc)

        await _wait_or_stop(stop_event, sched_cfg.poll_interval_seconds)


async def _run_scheduler_leader_tick(
    *,
    job_service: ScheduledJobService,
    uow_factory: Callable[[], IUnitOfWork],
    leases: LeaseManagerPort,
    worker_id: str,
    sched_cfg: Any,
    operations: Any,
    resource_version_gc_service: Optional["ResourceVersionGCService"],
    patrol_retention_service: Optional["PatrolRetentionService"],
) -> None:
    """One leader-only tick: reconcile, GC/retention ticks, and job triggers.

    Runs under a continuously renewed SCHEDULER_LEADER_KEY (see caller). Per-step
    try/excepts stay inside so a single sub-step failure does not forfeit
    leadership for the rest of the tick.
    """
    try:
        await job_service.reconcile_running_runs(
            limit=sched_cfg.max_concurrent_jobs,
        )
    except (OSError, RuntimeError, ValueError):
        logger.exception("Scheduled Run reconciliation failed")

    if operations.resource_gc.knowledge_base.enabled and resource_version_gc_service is not None:
        try:
            gc_result = await run_knowledge_version_gc_tick(
                resource_version_gc_service,
                leases=leases,
                worker_id=worker_id,
                lease_seconds=sched_cfg.leader_lease_seconds,
            )
            if gc_result is not None:
                logger.info(
                    "Knowledge-version GC tick metrics=%s",
                    gc_result.metrics(),
                )
        except (OSError, RuntimeError, ValueError):
            logger.exception("Knowledge-version GC tick failed")

    if patrol_retention_service is not None:
        try:
            retention_result = await run_patrol_retention_tick(
                patrol_retention_service,
                leases=leases,
                worker_id=worker_id,
                lease_seconds=sched_cfg.leader_lease_seconds,
            )
            if retention_result is not None and any(retention_result.values()):
                logger.info("Patrol retention tick metrics=%s", retention_result)
        except (OSError, RuntimeError, ValueError):
            logger.exception("Patrol retention tick failed")

    try:
        async with uow_factory() as uow:
            due_jobs = await uow.scheduled_job.list_due(
                datetime.now(UTC), limit=sched_cfg.max_concurrent_jobs
            )
        for job in due_jobs:
            if job.trigger_type == "webhook":
                continue
            try:
                await job_service.trigger_job(
                    job,
                    firing_id=(f"schedule:{job.id}:{job.next_run_at.isoformat()}"),
                    fired_at=job.next_run_at,
                )
                logger.info("Scheduler 触发 job=%s name=%s", job.id, job.name)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.exception("Scheduler 触发失败 job=%s", job.id)
                await job_service.record_trigger_failure(job, str(exc))
    except (OSError, RuntimeError, ValueError):
        logger.exception("Scheduler 轮询异常")


async def _wait_or_stop(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return
