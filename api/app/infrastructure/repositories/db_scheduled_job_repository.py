#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.scheduled_job import ScheduledJob
from app.domain.repositories.scheduled_job_repository import ScheduledJobRepository
from app.infrastructure.models.scheduled_job import ScheduledJobModel


class DBScheduledJobRepository(ScheduledJobRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, job: ScheduledJob) -> None:
        existing = await self.db_session.get(ScheduledJobModel, job.id)
        if existing:
            existing.update_from_domain(job)
        else:
            model = ScheduledJobModel()
            model.update_from_domain(job)
            model.id = job.id
            self.db_session.add(model)

    async def get_by_id(self, job_id: str) -> Optional[ScheduledJob]:
        row = await self.db_session.get(ScheduledJobModel, job_id)
        return row.to_domain() if row else None

    async def get_by_webhook_token(self, token: str) -> Optional[ScheduledJob]:
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.webhook_token == token)
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_by_owner(self, owner_user_id: str) -> List[ScheduledJob]:
        stmt = (
            select(ScheduledJobModel)
            .where(ScheduledJobModel.owner_user_id == owner_user_id)
            .order_by(ScheduledJobModel.updated_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def list_due(self, now: datetime, limit: int = 20) -> List[ScheduledJob]:
        stmt = (
            select(ScheduledJobModel)
            .where(
                ScheduledJobModel.enabled.is_(True),
                ScheduledJobModel.next_run_at.is_not(None),
                ScheduledJobModel.next_run_at <= now,
                ScheduledJobModel.last_run_status.is_distinct_from("running"),
            )
            .order_by(ScheduledJobModel.next_run_at.asc())
            .limit(limit)
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def get_by_last_run_session_id(self, session_id: str) -> Optional[ScheduledJob]:
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.last_run_session_id == session_id)
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None

    async def delete_by_id(self, job_id: str) -> None:
        await self.db_session.execute(delete(ScheduledJobModel).where(ScheduledJobModel.id == job_id))
