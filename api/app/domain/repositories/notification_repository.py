from typing import Protocol

from app.domain.models.notification import Notification, NotificationDelivery


class NotificationRepository(Protocol):
    async def save(self, notification: Notification) -> None: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        after_id: str | None = None,
    ) -> list[Notification]: ...

    async def mark_read(self, notification_id: str, user_id: str) -> None: ...

    async def count_unread(self, user_id: str) -> int: ...

    async def enqueue_delivery(self, delivery: NotificationDelivery) -> None: ...

    async def claim_deliveries(
        self, *, now, limit: int, lease_seconds: int
    ) -> list[NotificationDelivery]: ...

    async def finish_delivery(self, delivery: NotificationDelivery) -> None: ...

    async def list_deliveries(
        self, user_id: str, *, limit: int = 50
    ) -> list[NotificationDelivery]: ...

    async def retry_delivery(self, delivery_id: str, user_id: str, *, now) -> bool: ...

    async def get_delivery(self, delivery_id: str, user_id: str) -> NotificationDelivery | None: ...
