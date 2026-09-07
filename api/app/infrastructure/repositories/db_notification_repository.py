from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.notification import Notification, NotificationDelivery
from app.domain.repositories.notification_repository import NotificationRepository
from app.domain.utils.notification_message import encode_notification_message
from app.infrastructure.models.notification import NotificationDeliveryModel, NotificationModel


class DBNotificationRepository(NotificationRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, notification: Notification) -> None:
        await self.db_session.execute(
            insert(NotificationModel)
            .values(
                id=notification.id,
                user_id=notification.user_id,
                type=notification.type,
                session_id=notification.session_id,
                approval_id=notification.approval_id,
                artifact_id=notification.artifact_id,
                job_id=notification.job_id,
                message=encode_notification_message(
                    notification.message,
                    i18n_key=notification.i18n_key,
                    i18n_params=notification.i18n_params,
                ),
                read=notification.read,
                created_at=notification.created_at,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )

    async def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        after_id: str | None = None,
    ) -> list[Notification]:
        stmt = select(NotificationModel).where(NotificationModel.user_id == user_id)
        if unread_only:
            stmt = stmt.where(NotificationModel.read.is_(False))
        if after_id:
            after_row = await self.db_session.get(NotificationModel, after_id)
            if after_row:
                stmt = stmt.where(NotificationModel.created_at < after_row.created_at)
        stmt = stmt.order_by(NotificationModel.created_at.desc()).limit(limit)
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def mark_read(self, notification_id: str, user_id: str) -> None:
        stmt = (
            update(NotificationModel)
            .where(
                NotificationModel.id == notification_id,
                NotificationModel.user_id == user_id,
            )
            .values(read=True)
        )
        await self.db_session.execute(stmt)

    async def count_unread(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationModel)
            .where(NotificationModel.user_id == user_id, NotificationModel.read.is_(False))
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def enqueue_delivery(self, delivery: NotificationDelivery) -> None:
        await self.db_session.execute(
            insert(NotificationDeliveryModel)
            .values(**delivery.model_dump())
            .on_conflict_do_nothing(index_elements=["id"])
        )

    async def claim_deliveries(
        self, *, now, limit: int, lease_seconds: int
    ) -> list[NotificationDelivery]:
        stmt = (
            select(NotificationDeliveryModel)
            .where(
                or_(
                    and_(
                        NotificationDeliveryModel.status.in_(["pending", "retrying"]),
                        NotificationDeliveryModel.next_attempt_at <= now,
                    ),
                    and_(
                        NotificationDeliveryModel.status == "sending",
                        NotificationDeliveryModel.lease_until <= now,
                    ),
                )
            )
            .order_by(NotificationDeliveryModel.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self.db_session.execute(stmt)).scalars().all()
        for row in rows:
            row.status = "sending"
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=lease_seconds)
        await self.db_session.flush()
        return [row.to_domain() for row in rows]

    async def finish_delivery(self, delivery: NotificationDelivery) -> None:
        await self.db_session.execute(
            update(NotificationDeliveryModel)
            .where(
                NotificationDeliveryModel.id == delivery.id,
                NotificationDeliveryModel.status == "sending",
                NotificationDeliveryModel.attempts == delivery.attempts,
            )
            .values(
                status=delivery.status,
                last_error=delivery.last_error,
                next_attempt_at=delivery.next_attempt_at,
                lease_until=None,
                sent_at=delivery.sent_at,
            )
        )

    async def list_deliveries(self, user_id: str, *, limit: int = 50) -> list[NotificationDelivery]:
        rows = (
            (
                await self.db_session.execute(
                    select(NotificationDeliveryModel)
                    .where(NotificationDeliveryModel.user_id == user_id)
                    .order_by(NotificationDeliveryModel.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [row.to_domain() for row in rows]

    async def retry_delivery(self, delivery_id: str, user_id: str, *, now) -> bool:
        result = await self.db_session.execute(
            update(NotificationDeliveryModel)
            .where(
                NotificationDeliveryModel.id == delivery_id,
                NotificationDeliveryModel.user_id == user_id,
                NotificationDeliveryModel.status.in_(["failed", "retrying"]),
            )
            .values(
                status="pending",
                next_attempt_at=now,
                max_attempts=NotificationDeliveryModel.attempts + 5,
                last_error=None,
                lease_until=None,
            )
        )
        return result.rowcount == 1

    async def get_delivery(self, delivery_id: str, user_id: str) -> NotificationDelivery | None:
        row = await self.db_session.scalar(
            select(NotificationDeliveryModel).where(
                NotificationDeliveryModel.id == delivery_id,
                NotificationDeliveryModel.user_id == user_id,
            )
        )
        return row.to_domain() if row else None
