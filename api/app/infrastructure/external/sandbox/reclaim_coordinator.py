#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Leader-elected sandbox reclaim coordinator."""
from __future__ import annotations

import logging
import socket
import uuid

logger = logging.getLogger(__name__)

_LEADER_KEY = "sandbox:reclaim:leader"
_leader_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


async def try_become_reclaim_leader(lease_seconds: int) -> bool:
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        acquired = await redis.set(
            _LEADER_KEY,
            _leader_id,
            nx=True,
            ex=max(5, lease_seconds),
        )
        if acquired:
            return True
        owner = await redis.get(_LEADER_KEY)
        if owner == _leader_id:
            await redis.expire(_LEADER_KEY, max(5, lease_seconds))
            return True
        return False
    except Exception as exc:
        logger.debug("reclaim leader election failed: %s", exc)
        return False
