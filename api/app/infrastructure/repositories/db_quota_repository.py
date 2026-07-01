#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.user_quota import UserQuota
from app.domain.repositories.quota_repository import QuotaRepository
from app.infrastructure.models.user_quota import UserQuotaORM


class DBQuotaRepository(QuotaRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_for_user(self, user_id: str) -> Optional[UserQuota]:
        record = await self.db_session.get(UserQuotaORM, user_id)
        return record.to_domain() if record else None

    async def save(self, quota: UserQuota) -> None:
        record = await self.db_session.get(UserQuotaORM, quota.user_id)
        if record:
            record.update_from_domain(quota)
        else:
            self.db_session.add(UserQuotaORM.from_domain(quota))
