#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import Callable, Optional

from app.domain.models.notification import Notification
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)


class NotificationService:
    CHANNEL_PREFIX = "notify:"

    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def send(
            self,
            user_id: str,
            type: str,
            message: str,
            *,
            session_id: Optional[str] = None,
            artifact_id: Optional[str] = None,
            job_id: Optional[str] = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type,  # type: ignore[arg-type]
            message=message,
            session_id=session_id,
            artifact_id=artifact_id,
            job_id=job_id,
        )
        async with self._uow_factory() as uow:
            await uow.notification.save(notification)
            await uow.commit()

        try:
            redis = get_redis()
            await redis.client.publish(
                f"{self.CHANNEL_PREFIX}{user_id}",
                json.dumps(notification.model_dump(mode="json")),
            )
        except Exception as exc:
            logger.warning("通知 Redis 发布失败 user=%s: %s", user_id, exc)
        return notification

    async def list_for_user(
            self,
            user_id: str,
            *,
            unread_only: bool = False,
            limit: int = 50,
    ) -> list[Notification]:
        async with self._uow_factory() as uow:
            return await uow.notification.list_for_user(
                user_id, unread_only=unread_only, limit=limit,
            )

    async def mark_read(self, notification_id: str, user_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.notification.mark_read(notification_id, user_id)
            await uow.commit()

    async def count_unread(self, user_id: str) -> int:
        async with self._uow_factory() as uow:
            return await uow.notification.count_unread(user_id)

    async def send_im_via_mcp(
            self,
            owner_user_id: str,
            notify_channels: list,
            message: str,
            mcp_pool,
            app_config,
    ) -> None:
        if not notify_channels:
            return
        servers = app_config.mcp_config.mcpServers if app_config else {}
        for channel in notify_channels:
            server_name = channel.get("server_name") if isinstance(channel, dict) else getattr(channel, "server_name", "")
            if not server_name or server_name not in servers:
                continue
            cfg = servers[server_name]
            if not cfg.enabled:
                continue
            try:
                client = await mcp_pool.get_client(server_name, cfg)
                tools = await client.list_tools()
                send_tool = next(
                    (t for t in tools if "message" in t.name.lower() or "post" in t.name.lower()),
                    None,
                )
                if not send_tool:
                    continue
                channel_arg = channel.get("channel_arg") if isinstance(channel, dict) else getattr(channel, "channel_arg", "")
                await client.call_tool(send_tool.name, {"channel": channel_arg, "text": message, "message": message})
            except Exception as exc:
                logger.warning("IM 通知失败 server=%s user=%s: %s", server_name, owner_user_id, exc)
