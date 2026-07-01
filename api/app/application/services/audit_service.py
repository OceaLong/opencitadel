#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import io
from datetime import datetime
from typing import AsyncGenerator, Callable, Optional

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.uow import IUnitOfWork


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
