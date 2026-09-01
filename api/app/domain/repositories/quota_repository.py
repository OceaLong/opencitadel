from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models.user_quota import TeamQuota, UserQuota


class QuotaRepository(ABC):
    @abstractmethod
    async def get_for_user(self, user_id: str) -> UserQuota | None: ...

    @abstractmethod
    async def save(self, quota: UserQuota) -> None: ...

    @abstractmethod
    async def get_for_team(self, team_id: str) -> TeamQuota | None: ...

    @abstractmethod
    async def save_team(self, quota: TeamQuota) -> None: ...

    # -- 团队维度用量聚合（准入实时统计）--------------------------------------
    # 这些方法在当前授权上下文（UoW 的 RLS 作用域）下按 ``team_id`` 聚合，
    # 供 QuotaService 校验团队配额；个人维度用量仍走 QuotaUsageQueryPort。

    @abstractmethod
    async def team_daily_session_count(self, team_id: str, since: datetime) -> int: ...

    @abstractmethod
    async def team_monthly_token_sum(self, team_id: str, since: datetime) -> int: ...

    @abstractmethod
    async def team_storage_bytes(self, team_id: str) -> int: ...

    @abstractmethod
    async def user_monthly_token_sum(self, user_id: str, since: datetime) -> int: ...

    @abstractmethod
    async def count_active_sessions(
        self,
        *,
        user_id: str | None = None,
        team_id: str | None = None,
    ) -> int:
        """统计当前绑定了活跃执行 Run 的会话数（``active_execution_run_id`` 非空）。

        ``max_concurrent_tasks`` 准入用：按 ``user_id`` 或 ``team_id`` 过滤。
        """
        ...
