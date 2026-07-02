#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import io
from datetime import datetime
from typing import AsyncGenerator, Callable, Optional

from sqlalchemy import desc, func, select

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.audit_log import AuditLogORM


class AuditService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def record(self, log: AuditLog) -> None:
        async with self._uow_factory() as uow:
            await uow.audit.add(log)

    async def list_logs(
            self,
            *,
            actor_user_id: Optional[str] = None,
            action: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
            limit: int = 100,
            offset: int = 0,
    ) -> list[AuditLog]:
        async with self._uow_factory() as uow:
            return await uow.audit.list(
                actor_user_id=actor_user_id,
                action=action,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
                offset=offset,
            )

    async def summarize(
            self,
            *,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
    ) -> dict:
        async with self._uow_factory() as uow:
            day_bucket = func.date(AuditLogORM.created_at).label("date")
            day_stmt = select(day_bucket, func.count(AuditLogORM.id)).group_by(day_bucket).order_by(day_bucket)
            action_stmt = (
                select(AuditLogORM.action, func.count(AuditLogORM.id))
                .group_by(AuditLogORM.action)
                .order_by(desc(func.count(AuditLogORM.id)))
            )
            if start_at:
                day_stmt = day_stmt.where(AuditLogORM.created_at >= start_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at:
                day_stmt = day_stmt.where(AuditLogORM.created_at <= end_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at <= end_at)

            day_result = await uow.db_session.execute(day_stmt)  # type: ignore[attr-defined]
            action_result = await uow.db_session.execute(action_stmt)  # type: ignore[attr-defined]

        return {
            "by_day": [
                {"date": str(day), "count": int(count or 0)}
                for day, count in day_result.all()
            ],
            "by_action": [
                {"action": action, "count": int(count or 0)}
                for action, count in action_result.all()
            ],
        }

    async def export_csv(self) -> AsyncGenerator[str, None]:
        yield "id,actor_user_id,action,resource_type,resource_id,team_id,request_id,created_at\n"
        offset = 0
        while True:
            logs = await self.list_logs(limit=500, offset=offset)
            if not logs:
                break
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for log in logs:
                writer.writerow([
                    log.id,
                    log.actor_user_id or "",
                    log.action,
                    log.resource_type,
                    log.resource_id,
                    log.team_id or "",
                    log.request_id,
                    log.created_at.isoformat(),
                ])
            yield buffer.getvalue()
            offset += len(logs)
