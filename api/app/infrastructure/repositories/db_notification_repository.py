#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.notification import Notification
from app.domain.repositories.notification_repository import NotificationRepository
from app.domain.utils.notification_message import encode_notification_message
from app.infrastructure.models.notification import NotificationModel


class DBNotificationRepository(NotificationRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, notification: Notification) -> None:
        self.db_session.add(NotificationModel(
            id=notification.id,
            user_id=notification.user_id,
            type=notification.type,
            session_id=notification.session_id,
            artifact_id=notification.artifact_id,
            job_id=notification.job_id,
            message=encode_notification_message(
                notification.message,
                i18n_key=notification.i18n_key,
                i18n_params=notification.i18n_params,
            ),
            read=notification.read,
            created_at=notification.created_at,
        ))

    async def list_for_user(
            self,
            user_id: str,
            *,
            unread_only: bool = False,
            limit: int = 50,
            after_id: Optional[str] = None,
    ) -> List[Notification]:
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
