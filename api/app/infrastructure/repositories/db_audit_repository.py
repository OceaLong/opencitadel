import builtins
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
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


class DBAuditRepository(AuditRepository):
    def __init__(
        self,
        db_session: AsyncSession,
        *,
        signing_key: str,
        signing_key_id: str,
    ) -> None:
        if not signing_key:
            raise ValueError("audit signing key must not be empty")
        if not signing_key_id.strip():
            raise ValueError("audit signing key id must not be empty")
        self.db_session = db_session
        self._signing_key = signing_key
        self._signing_key_id = signing_key_id

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
        prev_hash = last.entry_hash if last and last.entry_hash else GENESIS

        fields = entry_fields(
            chain_seq=next_seq,
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            session_id=log.session_id,
            request_id=log.request_id,
            metadata=log.metadata,
            created_at=log.created_at,
        )
        entry_hash = compute_entry_hash(self._signing_key, fields, prev_hash)
        log.chain_seq = next_seq
        log.signing_key_id = self._signing_key_id
        log.prev_hash = prev_hash
        log.entry_hash = entry_hash
        self.db_session.add(AuditLogORM.from_domain(log))

    async def list(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if resource_type:
            stmt = stmt.where(AuditLogORM.resource_type == resource_type)
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
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

    async def get_by_id(self, log_id: str) -> AuditLog | None:
        stmt = select(AuditLogORM).where(AuditLogORM.id == log_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_chained(
        self,
        *,
        limit: int | None = None,
        resource_id: str | None = None,
        session_id: str | None = None,
    ) -> builtins.list[AuditLog]:
        stmt = select(AuditLogORM).where(AuditLogORM.chain_seq.isnot(None))
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
        stmt = stmt.order_by(AuditLogORM.chain_seq.asc())
        if limit is not None:
            stmt = stmt.limit(max(1, limit))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
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
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_by_actions(
        self,
        actions: builtins.list[str],
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        if not actions:
            return 0
        stmt = select(func.count()).select_from(AuditLogORM).where(AuditLogORM.action.in_(actions))
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_by_action_prefix(
        self,
        prefix: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        if not prefix:
            return 0
        stmt = (
            select(func.count())
            .select_from(AuditLogORM)
            .where(AuditLogORM.action.like(f"{prefix}%"))
        )
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_recent_chained(self, limit: int = 20) -> builtins.list[AuditLog]:
        # Same ordering key as the tail lookup in add() above (chain_seq
        # desc) -- chain_seq is the tamper-evident write-order sequence,
        # not created_at, so this sample is fit for checking whether
        # created_at tracks chain order rather than trivially agreeing with
        # itself. DESC + limit at the DB level (index-friendly), then
        # reverse in Python to hand callers ascending chain order.
        stmt = (
            select(AuditLogORM)
            .where(AuditLogORM.chain_seq.isnot(None))
            .order_by(AuditLogORM.chain_seq.desc())
            .limit(max(1, limit))
        )
        result = await self.db_session.execute(stmt)
        records = list(reversed(result.scalars().all()))
        return [record.to_domain() for record in records]

    async def daily_action_counts(
        self,
        actions: builtins.list[str],
        *,
        since: datetime | None = None,
    ) -> builtins.list[dict[str, Any]]:
        if not actions:
            return []
        date_col = func.date(AuditLogORM.created_at)
        stmt = select(date_col.label("date"), AuditLogORM.action, func.count()).where(
            AuditLogORM.action.in_(actions)
        )
        if since is not None:
            stmt = stmt.where(AuditLogORM.created_at >= since)
        stmt = stmt.group_by(date_col, AuditLogORM.action).order_by(date_col.asc())
        result = await self.db_session.execute(stmt)
        return [
            {"date": str(date_value), "action": action, "count": int(count)}
            for date_value, action, count in result.all()
        ]
