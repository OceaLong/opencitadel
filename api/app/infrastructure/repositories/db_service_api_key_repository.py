#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.service_api_key import ServiceApiKey
from app.domain.repositories.service_api_key_repository import ServiceApiKeyRepository
from app.infrastructure.models.service_api_key import ServiceApiKeyORM


class DBServiceApiKeyRepository(ServiceApiKeyRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_hash(self, key_hash: str) -> Optional[ServiceApiKey]:
        result = await self.db_session.execute(
            select(ServiceApiKeyORM).where(
                ServiceApiKeyORM.key_hash == key_hash,
                ServiceApiKeyORM.revoked_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_for_user(self, user_id: str) -> List[ServiceApiKey]:
        result = await self.db_session.execute(
            select(ServiceApiKeyORM)
            .where(ServiceApiKeyORM.owner_user_id == user_id)
            .order_by(ServiceApiKeyORM.created_at.desc())
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def save(self, key: ServiceApiKey) -> None:
        record = await self.db_session.get(ServiceApiKeyORM, key.id)
        if record:
            record.update_from_domain(key)
        else:
            self.db_session.add(ServiceApiKeyORM.from_domain(key))

    async def revoke(self, key_id: str, user_id: str) -> None:
        await self.db_session.execute(
            update(ServiceApiKeyORM)
            .where(
                ServiceApiKeyORM.id == key_id,
                ServiceApiKeyORM.owner_user_id == user_id,
                ServiceApiKeyORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now())
        )
