#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cross-process app config cache invalidation via Redis pub/sub."""
import asyncio
import logging
from typing import Optional

from app.application.services.config_provider import invalidate_runtime_config
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

CONFIG_INVALIDATE_CHANNEL = "app_config:invalidate"
_listener_task: Optional[asyncio.Task] = None


async def publish_config_invalidate() -> None:
    try:
        redis = get_redis()
        await redis.client.publish(CONFIG_INVALIDATE_CHANNEL, "1")
    except Exception as exc:
        logger.warning("发布配置失效通知失败: %s", exc)


async def _listen_config_invalidate() -> None:
    redis = get_redis()
    pubsub = redis.client.pubsub()
    await pubsub.subscribe(CONFIG_INVALIDATE_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            invalidate_runtime_config()
            logger.debug("收到跨进程配置失效通知，已刷新本地缓存")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("配置失效监听异常: %s", exc)
    finally:
        await pubsub.unsubscribe(CONFIG_INVALIDATE_CHANNEL)
        await pubsub.close()


async def start_config_invalidate_listener() -> None:
    global _listener_task
    if _listener_task is not None:
        return
    _listener_task = asyncio.create_task(_listen_config_invalidate())


async def stop_config_invalidate_listener() -> None:
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
