#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.audit_repository import AuditRepository
from app.infrastructure.models.audit_log import AuditLogORM


class DBAuditRepository(AuditRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add(self, log: AuditLog) -> None:
        self.db_session.add(AuditLogORM.from_domain(log))

    async def list(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        resource_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditLog]:
        stmt = select(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        stmt = (
            stmt.order_by(AuditLogORM.created_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 1000)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> int:
        stmt = select(func.count()).select_from(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)
