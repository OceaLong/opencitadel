#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, List, Optional

from app.domain.models.notification import Notification


class NotificationRepository(Protocol):
    async def save(self, notification: Notification) -> None: ...

    async def list_for_user(
            self,
            user_id: str,
            *,
            unread_only: bool = False,
            limit: int = 50,
            after_id: Optional[str] = None,
    ) -> List[Notification]: ...

    async def mark_read(self, notification_id: str, user_id: str) -> None: ...

    async def count_unread(self, user_id: str) -> int: ...
