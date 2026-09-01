from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.scheduled_job_repository import ScheduledJobRepository
from app.infrastructure.models.scheduled_job import ScheduledJobModel


class DBScheduledJobRepository(ScheduledJobRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(ScheduledJobModel.team_id == scope.team_id)
        return stmt.where(
            ScheduledJobModel.owner_user_id == scope.user_id,
            ScheduledJobModel.team_id.is_(None),
        )

    async def save(self, job: ScheduledJob) -> None:
        existing = await self.db_session.get(ScheduledJobModel, job.id)
        if existing:
            existing.update_from_domain(job)
        else:
            model = ScheduledJobModel()
            model.update_from_domain(job)
            model.id = job.id
            self.db_session.add(model)
            # Sessions are created with autoflush=False (see
            # infrastructure/storage/postgres.py), and ScheduledJobModel has no
            # ORM `relationship()` wiring to PatrolPackModel (only a Column-level
            # ForeignKey), so SQLAlchemy's flush-time insert ordering has no
            # dependency information to place this insert before a caller's
            # later, unrelated insert that references it (e.g.
            # PatrolPackService.create_pack() calling this then
            # DBPatrolRepository.save_pack() in the same transaction) if both
            # end up flushed together. Flushing here immediately makes this
            # write visible to any subsequent statement in the same
            # transaction regardless of add() order elsewhere.
            await self.db_session.flush()

    async def get_by_id(
        self,
        job_id: str,
        scope: OwnerScope | None = None,
        *,
        for_update: bool = False,
    ) -> ScheduledJob | None:
        stmt = self._apply_scope(
            select(ScheduledJobModel).where(ScheduledJobModel.id == job_id),
            scope,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_by_webhook_token(self, token: str) -> ScheduledJob | None:
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.webhook_token == token)
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_by_owner(self, owner_user_id: str) -> list[ScheduledJob]:
        stmt = (
            select(ScheduledJobModel)
            .where(ScheduledJobModel.owner_user_id == owner_user_id)
            .order_by(ScheduledJobModel.updated_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def list_for_scope(self, scope: OwnerScope) -> list[ScheduledJob]:
        stmt = self._apply_scope(select(ScheduledJobModel), scope).order_by(
            ScheduledJobModel.updated_at.desc()
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def list_due(self, now: datetime, limit: int = 20) -> list[ScheduledJob]:
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

    async def list_running(self, limit: int = 100) -> list[ScheduledJob]:
        stmt = (
            select(ScheduledJobModel)
            .where(
                ScheduledJobModel.last_run_status == "running",
                ScheduledJobModel.last_execution_run_id.is_not(None),
            )
            .order_by(ScheduledJobModel.last_run_at.asc())
            .limit(limit)
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def get_by_last_run_session_id(self, session_id: str) -> ScheduledJob | None:
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.last_run_session_id == session_id)
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None

    async def delete_by_id(self, job_id: str) -> None:
        await self.db_session.execute(
            delete(ScheduledJobModel).where(ScheduledJobModel.id == job_id)
        )
