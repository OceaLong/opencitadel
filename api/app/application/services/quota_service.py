from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.ports.queries import QuotaUsageQueryPort
from app.domain.errors import TooManyRequestsError
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork

_SESSION_WINDOW = timedelta(days=1)
_TOKEN_WINDOW = timedelta(days=30)


class QuotaService:
    """会话/存储/Token 与并发准入。

    多租户维度：个人 scope 仅校验用户配额；团队 scope 时**同时**独立校验用户
    配额与团队配额——任一超限即拒绝，等效于取两者的更严者作为有效上限。
    """

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        usage_query: QuotaUsageQueryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._usage_query = usage_query

    async def check_session_quota(
        self,
        user_id: str,
        scope: OwnerScope | None = None,
    ) -> None:
        """会话创建准入：日会话数 + 月 Token + 并发任务数（用户 + 团队）。"""
        team_id = _team_id(scope)
        now = datetime.now(UTC)
        session_since = now - _SESSION_WINDOW
        token_since = now - _TOKEN_WINDOW

        async with self._uow_factory() as uow:
            user_quota = await uow.quota.get_for_user(user_id)
            # 仅在配置了并发上限时才做活跃会话计数，避免给未设并发限额的用户
            # 在会话创建热路径上增加一次多余 COUNT。
            user_active = 0
            if user_quota is not None and user_quota.max_concurrent_tasks is not None:
                user_active = await uow.quota.count_active_sessions(user_id=user_id)
            team_quota = None
            team_daily_sessions = 0
            team_monthly_tokens = 0
            team_active = 0
            if team_id is not None:
                team_quota = await uow.quota.get_for_team(team_id)
                if team_quota is not None:
                    team_daily_sessions = await uow.quota.team_daily_session_count(
                        team_id, session_since
                    )
                    team_monthly_tokens = await uow.quota.team_monthly_token_sum(
                        team_id, token_since
                    )
                    if team_quota.max_concurrent_tasks is not None:
                        team_active = await uow.quota.count_active_sessions(team_id=team_id)

        if user_quota is not None:
            usage = await self._usage_query.snapshot(
                user_id=user_id,
                session_since=session_since,
                token_since=token_since,
            )
            _enforce_session_limits(
                user_quota.daily_session_limit,
                user_quota.monthly_token_limit,
                user_quota.max_concurrent_tasks,
                daily_sessions=usage.daily_sessions,
                monthly_tokens=usage.monthly_tokens,
                active_tasks=user_active,
                dimension="user",
            )

        if team_quota is not None:
            _enforce_session_limits(
                team_quota.daily_session_limit,
                team_quota.monthly_token_limit,
                team_quota.max_concurrent_tasks,
                daily_sessions=team_daily_sessions,
                monthly_tokens=team_monthly_tokens,
                active_tasks=team_active,
                dimension="team",
            )

    async def check_storage_quota(
        self,
        user_id: str,
        incoming_bytes: int = 0,
        scope: OwnerScope | None = None,
    ) -> None:
        """存储准入：用户存储上限；团队 scope 时同时校验团队存储上限。"""
        team_id = _team_id(scope)
        now = datetime.now(UTC)

        async with self._uow_factory() as uow:
            user_quota = await uow.quota.get_for_user(user_id)
            team_quota = None
            team_storage = 0
            if team_id is not None:
                team_quota = await uow.quota.get_for_team(team_id)
                if team_quota is not None and team_quota.max_storage_bytes is not None:
                    team_storage = await uow.quota.team_storage_bytes(team_id)

        if user_quota is not None and user_quota.max_storage_bytes is not None:
            usage = await self._usage_query.snapshot(
                user_id=user_id,
                session_since=now - _SESSION_WINDOW,
                token_since=now - _TOKEN_WINDOW,
            )
            if usage.storage_bytes + incoming_bytes > user_quota.max_storage_bytes:
                raise TooManyRequestsError(
                    "已达到存储容量上限",
                    error_key="errors.quota.storageLimit",
                    error_params={"scope": "user"},
                )

        if (
            team_quota is not None
            and team_quota.max_storage_bytes is not None
            and team_storage + incoming_bytes > team_quota.max_storage_bytes
        ):
            raise TooManyRequestsError(
                "已达到团队存储容量上限",
                error_key="errors.quota.storageLimit",
                error_params={"scope": "team"},
            )

    async def check_model_call_budget(
        self,
        *,
        user_id: str | None,
        team_id: str | None = None,
    ) -> None:
        """运行中模型调用准入：再次校验月 Token 上限（用户 + 团队）。

        会话创建时的一次性校验无法拦截长会话的持续超额，故在模型调用活动准入
        处复查已用 Token 是否已达上限。该方法只读、无副作用，可安全用于执行内核
        活动路径（见 ``ModelCallActivityHandler``）。
        """
        now = datetime.now(UTC)
        token_since = now - _TOKEN_WINDOW

        async with self._uow_factory() as uow:
            if user_id is not None:
                user_quota = await uow.quota.get_for_user(user_id)
                if user_quota is not None and user_quota.monthly_token_limit is not None:
                    used = await uow.quota.user_monthly_token_sum(user_id, token_since)
                    if used >= user_quota.monthly_token_limit:
                        raise TooManyRequestsError(
                            "已达到月度 Token 上限",
                            error_key="errors.quota.monthlyTokenLimit",
                            error_params={"scope": "user"},
                        )
            if team_id is not None:
                team_quota = await uow.quota.get_for_team(team_id)
                if team_quota is not None and team_quota.monthly_token_limit is not None:
                    used = await uow.quota.team_monthly_token_sum(team_id, token_since)
                    if used >= team_quota.monthly_token_limit:
                        raise TooManyRequestsError(
                            "已达到团队月度 Token 上限",
                            error_key="errors.quota.monthlyTokenLimit",
                            error_params={"scope": "team"},
                        )


def _team_id(scope: OwnerScope | None) -> str | None:
    if scope is None or scope.type != OwnerScopeType.TEAM:
        return None
    return scope.team_id


def _enforce_session_limits(
    daily_session_limit: int | None,
    monthly_token_limit: int | None,
    max_concurrent_tasks: int | None,
    *,
    daily_sessions: int,
    monthly_tokens: int,
    active_tasks: int,
    dimension: str,
) -> None:
    if daily_session_limit is not None and daily_sessions >= daily_session_limit:
        raise TooManyRequestsError(
            "已达到每日会话上限",
            error_key="errors.quota.dailySessionLimit",
            error_params={"scope": dimension},
        )
    if monthly_token_limit is not None and monthly_tokens >= monthly_token_limit:
        raise TooManyRequestsError(
            "已达到月度 Token 上限",
            error_key="errors.quota.monthlyTokenLimit",
            error_params={"scope": dimension},
        )
    if max_concurrent_tasks is not None and active_tasks >= max_concurrent_tasks:
        raise TooManyRequestsError(
            "已达到并发任务数上限",
            error_key="errors.quota.maxConcurrentTasks",
            error_params={"scope": dimension},
        )
