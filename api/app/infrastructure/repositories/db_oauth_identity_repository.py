#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.oauth_identity import OAuthIdentity
from app.domain.repositories.oauth_identity_repository import OAuthIdentityRepository
from app.infrastructure.models.oauth_identity import OAuthIdentityORM


class DBOAuthIdentityRepository(OAuthIdentityRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_provider_identity(self, provider: str, provider_user_id: str) -> Optional[OAuthIdentity]:
        stmt = select(OAuthIdentityORM).where(
            OAuthIdentityORM.provider == provider,
            OAuthIdentityORM.provider_user_id == provider_user_id,
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, identity: OAuthIdentity) -> None:
        record = await self.db_session.get(OAuthIdentityORM, identity.id)
        if record:
            record.update_from_domain(identity)
        else:
            self.db_session.add(OAuthIdentityORM.from_domain(identity))
