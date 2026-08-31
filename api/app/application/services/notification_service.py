import json
import logging
from collections.abc import Callable
from typing import Any

from app.application.ports.streams import NotificationPublisher
from app.application.services.integration_server_service import MCPServerService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.external.outbound_notifier import OutboundNotifierPort
from app.domain.models.notification import Notification, NotificationType
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        mcp_servers: MCPServerService,
        mcp_connection_pool: MCPConnectionPoolPort,
        policy_reader: PolicyHeadReader,
        publisher: NotificationPublisher,
        outbound_notifier: OutboundNotifierPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._mcp_servers = mcp_servers
        self._mcp_connection_pool = mcp_connection_pool
        self._policy_reader = policy_reader
        self._publisher = publisher
        self._outbound_notifier = outbound_notifier

    async def send(
        self,
        user_id: str,
        type: NotificationType,
        message: str,
        *,
        session_id: str | None = None,
        artifact_id: str | None = None,
        job_id: str | None = None,
        i18n_key: str | None = None,
        i18n_params: dict | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type,
            message=message,
            i18n_key=i18n_key,
            i18n_params=i18n_params,
            session_id=session_id,
            artifact_id=artifact_id,
            job_id=job_id,
        )
        async with self._uow_factory() as uow:
            await uow.notification.save(notification)
            await uow.commit()

        try:
            await self._publisher.publish(
                user_id,
                json.dumps(notification.model_dump(mode="json")),
            )
        except (OSError, RuntimeError, ValueError) as exc:
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
                user_id,
                unread_only=unread_only,
                limit=limit,
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
        scope: OwnerScope,
        notify_channels: list[dict[str, Any]],
        message: str,
    ) -> None:
        if not notify_channels:
            return
        try:
            execution = await self._policy_reader.active_execution(
                require_fresh=True,
                now=utc_now(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "IM notification denied by Runtime Policy user=%s: %s", owner_user_id, exc
            )
            return

        for channel in notify_channels:
            server_id = str(channel.get("server_id", "")).strip()
            if not server_id:
                continue
            try:
                runtime = await self._mcp_servers.resolve_mcp_runtime(
                    scope,
                    server_refs=(server_id,),
                )
                server = runtime.servers.get(server_id)
                if server is None or not server.enabled:
                    continue
                client = await self._mcp_connection_pool.acquire(
                    runtime,
                    policy=execution.revision.policy.activity,
                )
                tools = await client.get_all_tools()
                send_tool = next(
                    (
                        tool
                        for tool in tools
                        if "message" in str(tool.get("function", {}).get("name", "")).lower()
                        or "post" in str(tool.get("function", {}).get("name", "")).lower()
                    ),
                    None,
                )
                if not send_tool:
                    continue
                tool_name = str(send_tool["function"]["name"])
                await client.invoke(
                    tool_name,
                    {
                        "channel": str(channel.get("channel_arg", "")),
                        "text": message,
                        "message": message,
                    },
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("IM 通知失败 server=%s user=%s: %s", server_id, owner_user_id, exc)

    async def dispatch_notify_channels(
        self,
        owner_user_id: str,
        scope: OwnerScope,
        notify_channels: list[dict[str, Any]],
        message: str,
        *,
        subject: str = "OpenCitadel 通知",
    ) -> None:
        """Fan a notification out to every configured channel by type.

        mcp -> IM via MCP, webhook -> signed HTTP POST, email -> SMTP. Each
        channel is best-effort and isolated; one failure never blocks another.
        """
        if not notify_channels:
            return
        mcp_channels = [c for c in notify_channels if str(c.get("type", "mcp")) == "mcp"]
        if mcp_channels:
            await self.send_im_via_mcp(owner_user_id, scope, mcp_channels, message)
        if self._outbound_notifier is None:
            return
        for channel in notify_channels:
            ctype = str(channel.get("type", "mcp"))
            try:
                if ctype == "webhook" and channel.get("url"):
                    await self._outbound_notifier.send_webhook(
                        str(channel["url"]),
                        str(channel.get("secret", "")),
                        {"message": message, "user_id": owner_user_id},
                    )
                elif ctype == "email" and channel.get("address"):
                    await self._outbound_notifier.send_email(
                        str(channel["address"]), subject, message
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("%s 通知失败 user=%s: %s", ctype, owner_user_id, exc)
