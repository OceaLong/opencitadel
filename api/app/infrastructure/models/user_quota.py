from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.user_quota import TeamQuota, UserQuota

from .base import Base


class UserQuotaORM(Base):
    __tablename__ = "user_quotas"

    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    daily_session_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrent_tasks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, quota: UserQuota) -> "UserQuotaORM":
        return cls(
            user_id=quota.user_id,
            monthly_token_limit=quota.monthly_token_limit,
            daily_session_limit=quota.daily_session_limit,
            max_concurrent_tasks=quota.max_concurrent_tasks,
            max_storage_bytes=quota.max_storage_bytes,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
        )

    def update_from_domain(self, quota: UserQuota) -> None:
        self.monthly_token_limit = quota.monthly_token_limit
        self.daily_session_limit = quota.daily_session_limit
        self.max_concurrent_tasks = quota.max_concurrent_tasks
        self.max_storage_bytes = quota.max_storage_bytes
        self.updated_at = quota.updated_at

    def to_domain(self) -> UserQuota:
        return UserQuota(
            user_id=self.user_id,
            monthly_token_limit=self.monthly_token_limit,
            daily_session_limit=self.daily_session_limit,
            max_concurrent_tasks=self.max_concurrent_tasks,
            max_storage_bytes=self.max_storage_bytes,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class TeamQuotaORM(Base):
    """团队维度配额表，字段镜像 :class:`UserQuotaORM`，主键/外键为 ``team_id``。

    ``model_metadata.create_all`` 会自动建表（greenfield 干净重建覆盖）。
    TODO(tenant_rls): 本表尚未注册进 ``tenant_rls``，无行级隔离；应新增
    ``team_quotas`` 的团队作用域读策略（``team_id`` = 当前团队，写入限系统/管理员），
    详见 ``app/infrastructure/security/tenant_rls.py`` 中 ``user_quotas`` 的写法。
    """

    __tablename__ = "team_quotas"

    team_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    daily_session_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrent_tasks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, quota: TeamQuota) -> "TeamQuotaORM":
        return cls(
            team_id=quota.team_id,
            monthly_token_limit=quota.monthly_token_limit,
            daily_session_limit=quota.daily_session_limit,
            max_concurrent_tasks=quota.max_concurrent_tasks,
            max_storage_bytes=quota.max_storage_bytes,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
        )

    def update_from_domain(self, quota: TeamQuota) -> None:
        self.monthly_token_limit = quota.monthly_token_limit
        self.daily_session_limit = quota.daily_session_limit
        self.max_concurrent_tasks = quota.max_concurrent_tasks
        self.max_storage_bytes = quota.max_storage_bytes
        self.updated_at = quota.updated_at

    def to_domain(self) -> TeamQuota:
        return TeamQuota(
            team_id=self.team_id,
            monthly_token_limit=self.monthly_token_limit,
            daily_session_limit=self.daily_session_limit,
            max_concurrent_tasks=self.max_concurrent_tasks,
            max_storage_bytes=self.max_storage_bytes,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
