#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import socket
import uuid
from datetime import datetime
from typing import Callable, Optional

from app.application.services.config_provider import get_runtime_config
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

SCHEDULER_LEADER_KEY = "scheduler:leader"
_WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


async def try_become_scheduler_leader(lease_seconds: int) -> bool:
    """Acquire or renew leader lease only when this worker owns the key."""
    redis = get_redis()
    acquired = await redis.client.set(
        SCHEDULER_LEADER_KEY,
        _WORKER_ID,
        nx=True,
        ex=lease_seconds,
    )
    if acquired:
        return True
    current = await redis.client.get(SCHEDULER_LEADER_KEY)
    if current and current.decode() == _WORKER_ID:
        await redis.client.expire(SCHEDULER_LEADER_KEY, lease_seconds)
        return True
    return False


async def run_scheduler_loop(
        uow_factory: Callable[[], IUnitOfWork],
        job_service: ScheduledJobService,
        *,
        notification_service=None,
        mcp_pool=None,
        app_config=None,
        stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Worker background loop: poll due jobs and dispatch."""
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        config = get_runtime_config()
        sched_cfg = config.scheduler
        if not config.feature_flags.enable_scheduled_jobs or not sched_cfg.enabled:
            await asyncio.sleep(sched_cfg.poll_interval_seconds)
            continue

        if not await try_become_scheduler_leader(sched_cfg.leader_lease_seconds):
            await asyncio.sleep(sched_cfg.poll_interval_seconds)
            continue

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
