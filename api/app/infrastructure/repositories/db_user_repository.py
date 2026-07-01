#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.user import User
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.models.user import UserORM


class DBUserRepository(UserRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_id(self, user_id: str) -> Optional[User]:
        result = await self.db_session.execute(select(UserORM).where(UserORM.id == user_id))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db_session.execute(select(UserORM).where(UserORM.email == email.lower()))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.db_session.execute(select(UserORM).where(UserORM.username == username))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list(self, limit: int = 100, offset: int = 0) -> List[User]:
        stmt = (
            select(UserORM)
            .order_by(UserORM.created_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def save(self, user: User) -> None:
        user.email = user.email.lower()
        record = await self.db_session.get(UserORM, user.id)
        if record:
            record.update_from_domain(user)
        else:
            self.db_session.add(UserORM.from_domain(user))

    async def delete_by_id(self, user_id: str) -> None:
        await self.db_session.execute(delete(UserORM).where(UserORM.id == user_id))
