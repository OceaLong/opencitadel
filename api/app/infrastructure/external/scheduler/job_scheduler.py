#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import math
import socket
import uuid
from datetime import datetime
from typing import Callable, Optional, TYPE_CHECKING

from app.application.services.config_provider import get_runtime_config
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.redis import get_redis

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
_WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

_RENEW_SCHEDULER_LEASE = """
-- opencitadel:renew-scheduler-lease
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("PEXPIRE", KEYS[1], ARGV[2])
return 1
"""

_RELEASE_SCHEDULER_LEASE = """
-- opencitadel:release-scheduler-lease
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("DEL", KEYS[1])
return 1
"""


def _lease_ttl_milliseconds(lease_seconds: float) -> int:
    if lease_seconds <= 0:
        raise ValueError("scheduler lease must be positive")
    return max(1, math.ceil(lease_seconds * 1000))


async def acquire_scheduler_lease(
    key: str,
    owner_token: str,
    lease_seconds: float,
) -> bool:
    """Acquire one token-owned lease without replacing an existing owner."""
    if not key or not owner_token:
        return False
    try:
        return bool(
            await get_redis().client.set(
                key,
                owner_token,
                nx=True,
                px=_lease_ttl_milliseconds(lease_seconds),
            )
        )
    except Exception as exc:
        logger.warning("Scheduler lease acquire failed key=%s: %s", key, exc)
        return False


async def renew_scheduler_lease(
    key: str,
    owner_token: str,
    lease_seconds: float,
) -> bool:
    """Atomically renew only while the same token still owns the key."""
    if not key or not owner_token:
        return False
    try:
        renewed = await get_redis().client.eval(
            _RENEW_SCHEDULER_LEASE,
            1,
            key,
            owner_token,
            _lease_ttl_milliseconds(lease_seconds),
        )
        return int(renewed) == 1
    except Exception as exc:
        logger.warning("Scheduler lease renew failed key=%s: %s", key, exc)
        return False


async def release_scheduler_lease(
    key: str,
    owner_token: str,
) -> bool:
    """Atomically release only while the same token still owns the key."""
    if not key or not owner_token:
        return False
    try:
        released = await get_redis().client.eval(
            _RELEASE_SCHEDULER_LEASE,
            1,
            key,
            owner_token,
        )
        return int(released) == 1
    except Exception as exc:
        logger.warning("Scheduler lease release failed key=%s: %s", key, exc)
        return False


async def try_become_scheduler_leader(lease_seconds: int) -> bool:
    """Atomically acquire or token-check renewal of the worker leader lease."""
    if await acquire_scheduler_lease(
        SCHEDULER_LEADER_KEY,
        _WORKER_ID,
        lease_seconds,
    ):
        return True
    return await renew_scheduler_lease(
        SCHEDULER_LEADER_KEY,
        _WORKER_ID,
        lease_seconds,
    )


async def _keep_scheduler_lease_alive(
    key: str,
    owner_token: str,
    lease_seconds: float,
) -> None:
    interval = max(0.01, lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        if not await renew_scheduler_lease(
            key,
            owner_token,
            lease_seconds,
        ):
            raise RuntimeError(f"scheduler lease lost: {key}")


async def run_knowledge_version_gc_tick(
    service: "ResourceVersionGCService",
    *,
    retain_count: int,
    min_age_days: int,
    batch_size: int,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one GC transaction inside a token-owned, renewed cluster lease."""
    token = owner_token or f"{_WORKER_ID}:{uuid.uuid4().hex}"
    if not await acquire_scheduler_lease(
        KNOWLEDGE_VERSION_GC_LEASE_KEY,
        token,
        lease_seconds,
    ):
        return None

    collection = asyncio.create_task(
        service.collect_knowledge_versions(
            retain_count,
            min_age_days,
            batch_size,
        )
    )
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
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
        await release_scheduler_lease(
            KNOWLEDGE_VERSION_GC_LEASE_KEY,
            token,
        )


async def run_codebase_version_gc_tick(
    service: "ResourceVersionGCService",
    *,
    retain_count: int,
    min_age_days: int,
    batch_size: int,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one codebase GC transaction inside a token-owned cluster lease."""
    token = owner_token or f"{_WORKER_ID}:{uuid.uuid4().hex}"
    if not await acquire_scheduler_lease(
        CODEBASE_VERSION_GC_LEASE_KEY,
        token,
        lease_seconds,
    ):
        return None

    collection = asyncio.create_task(
        service.collect_codebase_versions(
            retain_count,
            min_age_days,
            batch_size,
        )
    )
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
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
        await release_scheduler_lease(
            CODEBASE_VERSION_GC_LEASE_KEY,
            token,
        )


async def run_patrol_retention_tick(
    service: "PatrolRetentionService",
    *,
    run_days: int,
    finding_days: int,
    evidence_days: int,
    batch_size: int,
    lease_seconds: float,
    owner_token: str | None = None,
):
    """Run one Patrol retention batch under an independently renewed lease."""
    token = owner_token or f"{_WORKER_ID}:{uuid.uuid4().hex}"
    if not await acquire_scheduler_lease(
        PATROL_RETENTION_LEASE_KEY,
        token,
        lease_seconds,
    ):
        return None
    collection = asyncio.create_task(
        service.cleanup(
            run_days=run_days,
            finding_days=finding_days,
            evidence_days=evidence_days,
            batch_size=batch_size,
        )
    )
    keepalive = asyncio.create_task(
        _keep_scheduler_lease_alive(
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
        await release_scheduler_lease(PATROL_RETENTION_LEASE_KEY, token)


async def run_scheduler_loop(
        uow_factory: Callable[[], IUnitOfWork],
        job_service: ScheduledJobService,
        *,
        notification_service=None,
        mcp_pool=None,
        app_config=None,
        resource_version_gc_service: Optional[
            "ResourceVersionGCService"
        ] = None,
        patrol_retention_service: Optional["PatrolRetentionService"] = None,
        stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Worker background loop: poll due jobs and dispatch."""
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        config = get_runtime_config()
        sched_cfg = config.scheduler
        if not sched_cfg.enabled:
            await asyncio.sleep(sched_cfg.poll_interval_seconds)
            continue

        if not await try_become_scheduler_leader(sched_cfg.leader_lease_seconds):
            await asyncio.sleep(sched_cfg.poll_interval_seconds)
            continue

        kb_cfg = config.knowledge_base
        if (
            kb_cfg.version_gc_enabled
            and resource_version_gc_service is not None
        ):
            try:
                result = await run_knowledge_version_gc_tick(
                    resource_version_gc_service,
                    retain_count=kb_cfg.version_retention_count,
                    min_age_days=kb_cfg.version_retention_min_days,
                    batch_size=kb_cfg.version_gc_batch_size,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None:
                    logger.info(
                        "Knowledge-version GC tick metrics=%s",
                        result.metrics(),
                    )
            except Exception as exc:
                logger.exception(
                    "Knowledge-version GC tick failed: %s",
                    exc,
                )

        codebase_cfg = getattr(config, "codebase", None)
        if (
            codebase_cfg is not None
            and codebase_cfg.version_gc_enabled
            and resource_version_gc_service is not None
        ):
            try:
                result = await run_codebase_version_gc_tick(
                    resource_version_gc_service,
                    retain_count=codebase_cfg.version_retention_count,
                    min_age_days=codebase_cfg.version_retention_min_days,
                    batch_size=codebase_cfg.version_gc_batch_size,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None:
                    logger.info(
                        "Codebase-version GC tick metrics=%s",
                        result.metrics(),
                    )
            except Exception as exc:
                logger.exception(
                    "Codebase-version GC tick failed: %s",
                    exc,
                )

        patrol_cfg = getattr(config, "patrol_retention", None)
        if patrol_cfg is not None and patrol_retention_service is not None:
            try:
                result = await run_patrol_retention_tick(
                    patrol_retention_service,
                    run_days=patrol_cfg.run_days,
                    finding_days=patrol_cfg.finding_days,
                    evidence_days=patrol_cfg.collector_evidence_days,
                    batch_size=patrol_cfg.cleanup_batch_size,
                    lease_seconds=sched_cfg.leader_lease_seconds,
                )
                if result is not None and any(result.values()):
                    logger.info("Patrol retention tick metrics=%s", result)
            except Exception as exc:
                logger.exception("Patrol retention tick failed: %s", exc)

        try:
            async with uow_factory() as uow:
                due_jobs = await uow.scheduled_job.list_due(datetime.now(), limit=sched_cfg.max_concurrent_jobs)
            for job in due_jobs:
                if job.trigger_type == "webhook":
                    continue
                try:
                    await job_service.trigger_job(
                        job,
                        notification_service=notification_service,
                        mcp_pool=mcp_pool,
                        app_config=app_config,
                    )
                    logger.info("Scheduler 触发 job=%s name=%s", job.id, job.name)
                except Exception as exc:
                    logger.exception("Scheduler 触发失败 job=%s: %s", job.id, exc)
                    await job_service.record_trigger_failure(job, str(exc))
        except Exception as exc:
            logger.exception("Scheduler 轮询异常: %s", exc)

        await asyncio.sleep(sched_cfg.poll_interval_seconds)
