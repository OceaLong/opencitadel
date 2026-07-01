#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, delete, update, or_, and_
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.memory_entry import MemoryEntry, MemoryScope
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.memory_entry_repository import MemoryEntryRepository
from app.infrastructure.models.memory_entry import MemoryEntryORM
from app.infrastructure.models.session import SessionModel


class DBMemoryEntryRepository(MemoryEntryRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _scope_conditions(self, owner_scope: Optional[OwnerScope]):
        if owner_scope is None:
            return []
        if owner_scope.type == OwnerScopeType.TEAM:
            return [MemoryEntryORM.team_id == owner_scope.team_id]
        return [MemoryEntryORM.owner_user_id == owner_scope.user_id, MemoryEntryORM.team_id.is_(None)]

    async def get_all(
            self,
            scope: Optional[MemoryScope] = None,
            session_id: Optional[str] = None,
            q: Optional[str] = None,
            tags: Optional[List[str]] = None,
            limit: int = 100,
            owner_scope: Optional[OwnerScope] = None,
    ) -> List[MemoryEntry]:
        stmt = select(MemoryEntryORM).order_by(
            MemoryEntryORM.last_used_at.desc().nullslast(),
            MemoryEntryORM.created_at.desc(),
        ).limit(limit)
        conditions = self._scope_conditions(owner_scope)
        if scope:
            conditions.append(MemoryEntryORM.scope == scope.value)
        if session_id:
            conditions.append(MemoryEntryORM.session_id == session_id)
        if q:
            conditions.append(MemoryEntryORM.title.ilike(f"%{q}%"))
        if tags:
            conditions.append(MemoryEntryORM.tags.op("?|")(array(tags)))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_by_id(self, entry_id: str, owner_scope: Optional[OwnerScope] = None) -> Optional[MemoryEntry]:
        stmt = select(MemoryEntryORM).where(MemoryEntryORM.id == entry_id, *self._scope_conditions(owner_scope))
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def recall_for_session(self, session_id: str, limit: int = 20) -> List[MemoryEntry]:
        fetch_limit = max(limit * 3, limit)
        session = await self.db_session.scalar(
            select(SessionModel).where(SessionModel.id == session_id).limit(1)
        )
        if session is None:
            return []
        if session.team_id:
            owner_conditions = [MemoryEntryORM.team_id == session.team_id]
        else:
            owner_conditions = [
                MemoryEntryORM.owner_user_id == session.owner_user_id,
                MemoryEntryORM.team_id.is_(None),
            ]
        stmt = (
            select(MemoryEntryORM)
            .where(
                and_(
                    *owner_conditions,
                    or_(
                        MemoryEntryORM.scope == MemoryScope.GLOBAL.value,
                        and_(
                            MemoryEntryORM.scope == MemoryScope.SESSION.value,
                            MemoryEntryORM.session_id == session_id,
                        ),
                    ),
                )
            )
            .order_by(
                MemoryEntryORM.last_used_at.desc().nullslast(),
                MemoryEntryORM.use_count.desc(),
                MemoryEntryORM.created_at.desc(),
            )
            .limit(fetch_limit)
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def save(self, entry: MemoryEntry) -> None:
        stmt = select(MemoryEntryORM).where(MemoryEntryORM.id == entry.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        entry.updated_at = datetime.now()
        if record:
            record.scope = entry.scope.value
            record.session_id = entry.session_id
            record.title = entry.title
            record.content = entry.content
            record.tags = entry.tags
            record.owner_user_id = entry.owner_user_id
            record.team_id = entry.team_id
            record.source = entry.source.value
            record.last_used_at = entry.last_used_at
            record.use_count = entry.use_count
            record.updated_at = entry.updated_at
        else:
            self.db_session.add(MemoryEntryORM.from_domain(entry))

    async def delete_by_id(self, entry_id: str, owner_scope: Optional[OwnerScope] = None) -> None:
        await self.db_session.execute(
            delete(MemoryEntryORM).where(MemoryEntryORM.id == entry_id, *self._scope_conditions(owner_scope))
        )

    async def touch_used(self, entry_ids: List[str]) -> None:
        if not entry_ids:
            return
        await self.db_session.execute(
            update(MemoryEntryORM)
            .where(MemoryEntryORM.id.in_(entry_ids))
            .values(
                last_used_at=datetime.now(),
                use_count=MemoryEntryORM.use_count + 1,
            )
        )
