#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sandbox idle/low-memory maintenance with quota reconcile."""
from __future__ import annotations

import logging
import time
from typing import Iterable

from app.infrastructure.external.runtime_settings import get_admission_runtime_settings
from app.infrastructure.external.sandbox.admission import get_sandbox_quota
from app.infrastructure.external.sandbox.memory_probe import get_host_available_mb
from app.infrastructure.external.sandbox.reclaim_coordinator import try_become_reclaim_leader
from app.infrastructure.external.sandbox.sandbox_driver import get_sandbox_class

logger = logging.getLogger(__name__)


async def run_sandbox_maintenance() -> int:
    """Reconcile quota, run idle cleanup, optionally reclaim under memory pressure."""
    admission = get_admission_runtime_settings()
    sandbox_cls = get_sandbox_class()
    quota = get_sandbox_quota()

    live_ids = await sandbox_cls.list_live_sandbox_ids()
    await quota.reconcile(live_ids)

    if not await try_become_reclaim_leader(admission.reclaim_leader_lease_seconds):
        return 0

    removed = await sandbox_cls.cleanup_orphaned_containers()
    if not admission.admission_reclaim_enabled:
        return removed

    available = get_host_available_mb()
    if available is None or available >= admission.admission_min_host_available_mb:
        return removed

    extra = await _reclaim_idle_for_memory(
        sandbox_cls,
        target_mb=admission.admission_reclaim_target_mb,
        min_mb=admission.admission_min_host_available_mb,
    )
    live_after = await sandbox_cls.list_live_sandbox_ids()
    await quota.reconcile(live_after)
    return removed + extra


async def _reclaim_idle_for_memory(sandbox_cls, target_mb: int, min_mb: int) -> int:
    """Remove longest-idle sandboxes until memory recovers."""
    from app.infrastructure.storage.redis import get_redis

    removed = 0
    try:
        redis = get_redis().client
    except Exception:
        return 0

    candidates: list[tuple[int, str]] = []
    live = await sandbox_cls.list_live_sandbox_ids()
    now = int(time.time())
    for sid in live:
        raw = await redis.get(f"sandbox:last_active:{sid}")
        last_active = int(raw) if raw else 0
        candidates.append((last_active or 0, sid))
    candidates.sort(key=lambda x: x[0])

    quota = get_sandbox_quota()
    for _, sandbox_id in candidates:
        available = get_host_available_mb()
        if available is not None and available >= target_mb:
            break
        if available is not None and available >= min_mb and removed > 0:
            break
        sandbox = await sandbox_cls.get(sandbox_id)
        if not sandbox:
            await quota.release(sandbox_id)
            continue
        if await sandbox.destroy():
            removed += 1
            from app.infrastructure.observability.admission_metrics import record_sandbox_reclaimed

            record_sandbox_reclaimed("low_memory")
            logger.info("Low-memory reclaimed idle sandbox: %s", sandbox_id)
    return removed
