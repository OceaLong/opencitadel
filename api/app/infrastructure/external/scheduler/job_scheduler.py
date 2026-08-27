import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

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

logger = logging.getLogger(__name__)

SCHEDULER_LEADER_KEY = "scheduler:leader"
KNOWLEDGE_VERSION_GC_LEASE_KEY = "scheduler:knowledge-version-gc"
CODEBASE_VERSION_GC_LEASE_KEY = "scheduler:codebase-version-gc"
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

    collection = asyncio.create_task(service.collect_knowledge_versions())
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
            leases,
            KNOWLEDGE_VERSION_GC_LEASE_KEY,
            token,
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
        raise RuntimeError("knowledge-version GC lease ended unexpectedly")
    finally:
        if not collection.done():
            collection.cancel()
            await asyncio.gather(collection, return_exceptions=True)
        keepalive.cancel()
        await asyncio.gather(keepalive, return_exceptions=True)
        await leases.release(
            KNOWLEDGE_VERSION_GC_LEASE_KEY,
            token,
        )


async def run_codebase_version_gc_tick(
    service: "ResourceVersionGCService",
    *,
    leases: LeaseManagerPort,
    worker_id: str,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one codebase GC transaction inside a token-owned cluster lease."""
    token = owner_token or f"{worker_id}:{uuid.uuid4().hex}"
    if not await leases.acquire(
        CODEBASE_VERSION_GC_LEASE_KEY,
        token,
        ttl_seconds=lease_seconds,
    ):
        return None

    collection = asyncio.create_task(service.collect_codebase_versions())
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
            leases,
            CODEBASE_VERSION_GC_LEASE_KEY,
            token,
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
        raise RuntimeError("codebase-version GC lease ended unexpectedly")
    finally:
        if not collection.done():
            collection.cancel()
            await asyncio.gather(collection, return_exceptions=True)
        keepalive.cancel()
        await asyncio.gather(keepalive, return_exceptions=True)
        await leases.release(
            CODEBASE_VERSION_GC_LEASE_KEY,
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
    collection = asyncio.create_task(service.cleanup())
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
            leases,
            PATROL_RETENTION_LEASE_KEY,
            token,
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
        raise RuntimeError("Patrol retention lease ended unexpectedly")
    finally:
        if not collection.done():
            collection.cancel()
            await asyncio.gather(collection, return_exceptions=True)
        keepalive.cancel()
        await asyncio.gather(keepalive, return_exceptions=True)
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

        if not await try_become_scheduler_leader(
            leases,
            worker_id=worker_id,
            lease_seconds=sched_cfg.leader_lease_seconds,
        ):
            await _wait_or_stop(stop_event, sched_cfg.poll_interval_seconds)
            continue

        try:
            await job_service.reconcile_running_runs(
                limit=sched_cfg.max_concurrent_jobs,
            )
        except (OSError, RuntimeError, ValueError):
            logger.exception("Scheduled Run reconciliation failed")

        if (
            operations.resource_gc.knowledge_base.enabled
            and resource_version_gc_service is not None
        ):
            try:
                result = await run_knowledge_version_gc_tick(
                    resource_version_gc_service,
                    leases=leases,
                    worker_id=worker_id,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None:
                    logger.info(
                        "Knowledge-version GC tick metrics=%s",
                        result.metrics(),
                    )
            except (OSError, RuntimeError, ValueError):
                logger.exception("Knowledge-version GC tick failed")

        if operations.resource_gc.codebase.enabled and resource_version_gc_service is not None:
            try:
                result = await run_codebase_version_gc_tick(
                    resource_version_gc_service,
                    leases=leases,
                    worker_id=worker_id,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None:
                    logger.info(
                        "Codebase-version GC tick metrics=%s",
                        result.metrics(),
                    )
            except (OSError, RuntimeError, ValueError):
                logger.exception("Codebase-version GC tick failed")

        if patrol_retention_service is not None:
            try:
                result = await run_patrol_retention_tick(
                    patrol_retention_service,
                    leases=leases,
                    worker_id=worker_id,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None and any(result.values()):
                    logger.info("Patrol retention tick metrics=%s", result)
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

        await _wait_or_stop(stop_event, sched_cfg.poll_interval_seconds)


async def _wait_or_stop(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return
