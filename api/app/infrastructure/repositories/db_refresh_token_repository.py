#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.refresh_token import RefreshToken
from app.domain.repositories.refresh_token_repository import RefreshTokenRepository
from app.infrastructure.models.refresh_token import RefreshTokenORM


class DBRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self.db_session.execute(
            select(RefreshTokenORM).where(RefreshTokenORM.token_hash == token_hash)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, token: RefreshToken) -> None:
        record = await self.db_session.get(RefreshTokenORM, token.id)
        if record:
            record.update_from_domain(token)
        else:
            self.db_session.add(RefreshTokenORM.from_domain(token))

    async def revoke_by_hash(self, token_hash: str) -> None:
        await self.db_session.execute(
            update(RefreshTokenORM)
            .where(RefreshTokenORM.token_hash == token_hash)
            .values(revoked_at=datetime.now())
        )

    async def consume_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self.db_session.execute(
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.token_hash == token_hash,
                RefreshTokenORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now())
            .returning(RefreshTokenORM)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def revoke_all_for_user(self, user_id: str) -> None:
        await self.db_session.execute(
            update(RefreshTokenORM)
            .where(RefreshTokenORM.user_id == user_id, RefreshTokenORM.revoked_at.is_(None))
            .values(revoked_at=datetime.now())
        )
