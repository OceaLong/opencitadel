from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.user_quota import TeamQuota, UserQuota
from app.domain.repositories.quota_repository import QuotaRepository
from app.infrastructure.models.file import FileModel
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.user_quota import TeamQuotaORM, UserQuotaORM


class DBQuotaRepository(QuotaRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_for_user(self, user_id: str) -> UserQuota | None:
        record = await self.db_session.get(UserQuotaORM, user_id)
        return record.to_domain() if record else None

    async def save(self, quota: UserQuota) -> None:
        record = await self.db_session.get(UserQuotaORM, quota.user_id)
        if record:
            record.update_from_domain(quota)
        else:
            self.db_session.add(UserQuotaORM.from_domain(quota))

    async def get_for_team(self, team_id: str) -> TeamQuota | None:
        record = await self.db_session.get(TeamQuotaORM, team_id)
        return record.to_domain() if record else None

    async def save_team(self, quota: TeamQuota) -> None:
        record = await self.db_session.get(TeamQuotaORM, quota.team_id)
        if record:
            record.update_from_domain(quota)
        else:
            self.db_session.add(TeamQuotaORM.from_domain(quota))

    async def team_daily_session_count(self, team_id: str, since: datetime) -> int:
        value = await self.db_session.scalar(
            select(func.count(SessionModel.id)).where(
                SessionModel.team_id == team_id,
                SessionModel.created_at >= since,
            )
        )
        return int(value or 0)

    async def team_monthly_token_sum(self, team_id: str, since: datetime) -> int:
        value = await self.db_session.scalar(
            select(func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0)).where(
                LLMTokenUsageORM.team_id == team_id,
                LLMTokenUsageORM.created_at >= since,
            )
        )
        return int(value or 0)

    async def team_storage_bytes(self, team_id: str) -> int:
        value = await self.db_session.scalar(
            select(func.coalesce(func.sum(FileModel.size), 0)).where(FileModel.team_id == team_id)
        )
        return int(value or 0)

    async def user_monthly_token_sum(self, user_id: str, since: datetime) -> int:
        value = await self.db_session.scalar(
            select(func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0)).where(
                LLMTokenUsageORM.owner_user_id == user_id,
                LLMTokenUsageORM.created_at >= since,
            )
        )
        return int(value or 0)

    async def count_active_sessions(
        self,
        *,
        user_id: str | None = None,
        team_id: str | None = None,
    ) -> int:
        statement = select(func.count(SessionModel.id)).where(
            SessionModel.active_execution_run_id.is_not(None),
            SessionModel.deleted_at.is_(None),
        )
        if team_id is not None:
            statement = statement.where(SessionModel.team_id == team_id)
        elif user_id is not None:
            statement = statement.where(SessionModel.owner_user_id == user_id)
        value = await self.db_session.scalar(statement)
        return int(value or 0)
