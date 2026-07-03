#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.audit_repository import AuditRepository
from app.domain.services.audit_chain import (
    ADVISORY_LOCK_KEY,
    GENESIS,
    compute_entry_hash,
    entry_fields,
)
from app.infrastructure.models.audit_log import AuditLogORM
from core.config import get_settings


class DBAuditRepository(AuditRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add(self, log: AuditLog) -> None:
        await self.db_session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": ADVISORY_LOCK_KEY},
        )
        last_stmt = (
            select(AuditLogORM.chain_seq, AuditLogORM.entry_hash)
            .where(AuditLogORM.chain_seq.isnot(None))
            .order_by(AuditLogORM.chain_seq.desc())
            .limit(1)
        )
        result = await self.db_session.execute(last_stmt)
        last = result.first()
        next_seq = (last.chain_seq if last and last.chain_seq else 0) + 1
        prev_hash = (last.entry_hash if last and last.entry_hash else GENESIS)

        secret = get_settings().api_key_secret
        fields = entry_fields(
            chain_seq=next_seq,
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            request_id=log.request_id,
            metadata=log.metadata,
            created_at=log.created_at,
        )
        entry_hash = compute_entry_hash(secret, fields, prev_hash)
        log.chain_seq = next_seq
        log.prev_hash = prev_hash
        log.entry_hash = entry_hash
        self.db_session.add(AuditLogORM.from_domain(log))

    async def list(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
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
        if resource_type:
            stmt = stmt.where(AuditLogORM.resource_type == resource_type)
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

    async def get_by_id(self, log_id: str) -> Optional[AuditLog]:
        stmt = select(AuditLogORM).where(AuditLogORM.id == log_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_chained(
        self,
        *,
        limit: Optional[int] = None,
        resource_id: Optional[str] = None,
    ) -> List[AuditLog]:
        stmt = select(AuditLogORM).where(AuditLogORM.chain_seq.isnot(None))
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        stmt = stmt.order_by(AuditLogORM.chain_seq.asc())
        if limit is not None:
            stmt = stmt.limit(max(1, limit))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> int:
        stmt = select(func.count()).select_from(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if resource_type:
            stmt = stmt.where(AuditLogORM.resource_type == resource_type)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)
