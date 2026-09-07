import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from app.application.ports.streams import NotificationPublisher
from app.application.services.integration_server_service import MCPServerService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.external.outbound_notifier import OutboundNotifierPort
from app.domain.models.notification import Notification, NotificationDelivery, NotificationType
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
        *,
        delivery_id: str | None = None,
    ) -> None:
        from app.domain.models.scheduled_job import NotifyChannel

        for raw in notify_channels:
            channel = NotifyChannel.model_validate(raw)
            client, arguments = await self._prepare_mcp_channel(
                scope, channel, message, delivery_id
            )
            result = await client.invoke(channel.tool_name, arguments)
            if (
                (
                    isinstance(result, dict)
                    and (result.get("isError") or result.get("success") is False)
                )
                or getattr(result, "isError", False)
                or getattr(result, "success", True) is False
            ):
                raise RuntimeError("Notification MCP tool reported delivery failure")

    async def validate_channel(self, scope: OwnerScope, channel) -> None:
        """Resolve and validate the configured send contract without invoking it."""
        if channel.type == "mcp":
            await self._prepare_mcp_channel(
                scope, channel, "OpenCitadel configuration check", "configuration-check"
            )

    async def _prepare_mcp_channel(self, scope, channel, message, delivery_id):
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError

        execution = await self._policy_reader.active_execution(require_fresh=True, now=utc_now())
        runtime = await self._mcp_servers.resolve_mcp_runtime(
            scope, server_refs=(channel.server_id,)
        )
        server = runtime.servers.get(channel.server_id)
        if server is None or not server.enabled:
            raise ValueError("Notification MCP server is unavailable")
        client = await self._mcp_connection_pool.acquire(
            runtime, policy=execution.revision.policy.activity
        )
        tools = await client.get_all_tools()
        matches = [
            tool["function"]
            for tool in tools
            if tool.get("function", {}).get("name") == channel.tool_name
        ]
        if len(matches) != 1:
            raise ValueError("Configured notification tool is unavailable or ambiguous")
        schema = matches[0].get("parameters")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("Notification tool requires an object input schema")

        def reject_external_references(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        key in {"$ref", "$dynamicRef"}
                        and isinstance(child, str)
                        and not child.startswith("#")
                    ):
                        raise ValueError(
                            "External schema references are not allowed for notification tools"
                        )
                    reject_external_references(child)
            elif isinstance(value, list):
                for child in value:
                    reject_external_references(child)

        reject_external_references(schema)
        arguments = {**channel.arguments, channel.message_arg: message}
        if channel.idempotency_arg and delivery_id:
            arguments[channel.idempotency_arg] = delivery_id
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
        except (SchemaError, ValidationError) as exc:
            raise ValueError(
                "Notification arguments do not match the configured tool schema"
            ) from exc
        return client, arguments

    async def queue_channels(
        self,
        uow: IUnitOfWork,
        owner_user_id: str,
        scope: OwnerScope,
        notify_channels: list[dict[str, Any]],
        message: str,
        *,
        idempotency_key: str,
        subject: str = "OpenCitadel notification",
    ) -> list[str]:
        from app.domain.models.scheduled_job import NotifyChannel

        delivery_ids = []
        for index, raw in enumerate(notify_channels):
            channel = NotifyChannel.model_validate(raw).model_dump()
            key = hashlib.sha256(f"{owner_user_id}:{idempotency_key}:{index}".encode()).hexdigest()
            delivery_ids.append(key)
            await uow.notification.enqueue_delivery(
                NotificationDelivery(
                    id=key,
                    user_id=owner_user_id,
                    scope=scope.model_dump(mode="json"),
                    channel=channel,
                    message=message,
                    subject=subject,
                )
            )

        return delivery_ids

    async def dispatch_notify_channels(
        self,
        owner_user_id: str,
        scope: OwnerScope,
        notify_channels: list[dict[str, Any]],
        message: str,
        *,
        subject: str = "OpenCitadel notification",
        idempotency_key: str | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await self.queue_channels(
                uow,
                owner_user_id,
                scope,
                notify_channels,
                message,
                idempotency_key=idempotency_key or str(uuid.uuid4()),
                subject=subject,
            )
            await uow.commit()

    async def queue_test_channel(self, scope, channel, *, request_id: str) -> str:
        async with self._uow_factory() as uow:
            ids = await self.queue_channels(
                uow,
                scope.user_id,
                scope,
                [channel.model_dump()],
                "OpenCitadel test notification",
                idempotency_key=f"test:{request_id}",
            )
            await uow.commit()
            return ids[0]

    async def process_deliveries(self, *, limit: int = 50) -> int:
        # Claim one at a time: a batch must not outlive its lease while waiting for earlier sends.
        processed = 0
        while processed < limit:
            async with self._uow_factory() as uow:
                rows = await uow.notification.claim_deliveries(
                    now=utc_now(), limit=1, lease_seconds=90
                )
                await uow.commit()
            if not rows:
                break
            delivery = rows[0]
            try:
                async with asyncio.timeout(60):
                    await self._deliver(delivery)
            except Exception as exc:  # noqa: BLE001 — durable worker isolates third-party adapter failures
                # Do not persist adapter errors: they can contain channel secrets or message contents.
                delivery.last_error = (
                    type(exc).__name__
                    + ": delivery failed; verify channel configuration and connectivity"
                )
                delivery.status = (
                    "failed" if delivery.attempts >= delivery.max_attempts else "retrying"
                )
                delivery.next_attempt_at = utc_now() + timedelta(
                    seconds=min(
                        3600,
                        30 * 2 ** max(0, min(delivery.attempts - delivery.max_attempts + 4, 7)),
                    )
                )
            else:
                delivery.status = "sent"
                delivery.last_error = None
                delivery.sent_at = utc_now()
            async with self._uow_factory() as uow:
                await uow.notification.finish_delivery(delivery)
                await uow.commit()
            processed += 1
        return processed

    async def _deliver(self, delivery: NotificationDelivery) -> None:
        channel = delivery.channel
        if channel["type"] == "mcp":
            await self.send_im_via_mcp(
                delivery.user_id,
                OwnerScope.model_validate(delivery.scope),
                [channel],
                delivery.message,
                delivery_id=delivery.id,
            )
        elif self._outbound_notifier is None:
            raise RuntimeError("Outbound notifier unavailable")
        elif channel["type"] == "webhook":
            await self._outbound_notifier.send_webhook(
                channel["url"],
                channel["secret"],
                {
                    "message": delivery.message,
                    "user_id": delivery.user_id,
                    "delivery_id": delivery.id,
                },
            )
        elif channel["type"] == "email":
            await self._outbound_notifier.send_email(
                channel["address"], delivery.subject, delivery.message, delivery_id=delivery.id
            )
        else:
            raise ValueError("Unsupported notification channel")

    async def list_deliveries(self, user_id: str, *, limit: int = 50) -> list[NotificationDelivery]:
        async with self._uow_factory() as uow:
            return await uow.notification.list_deliveries(user_id, limit=limit)

    async def retry_delivery(self, delivery_id: str, user_id: str) -> bool:
        async with self._uow_factory() as uow:
            retried = await uow.notification.retry_delivery(delivery_id, user_id, now=utc_now())
            await uow.commit()
            return retried

    async def get_delivery(self, delivery_id: str, user_id: str) -> NotificationDelivery | None:
        async with self._uow_factory() as uow:
            return await uow.notification.get_delivery(delivery_id, user_id)
