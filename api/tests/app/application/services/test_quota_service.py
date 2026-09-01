"""QuotaService 单测：per-user / per-team / 更严者 / 并发上限 / 运行中 Token 拦截 / 边界。

配额是唯一曾无单测的 application service。这里用轻量 fake（内存 UoW + quota 仓储 +
usage 查询端口）覆盖准入决策逻辑，不依赖真实 DB。
"""

from __future__ import annotations

from typing import Self

import pytest

from app.application.ports.queries import QuotaUsageSnapshot
from app.application.services.quota_service import QuotaService
from app.domain.errors import TooManyRequestsError
from app.domain.models.scope import OwnerScope
from app.domain.models.user_quota import TeamQuota, UserQuota


class _FakeQuotaRepo:
    def __init__(
        self,
        *,
        user_quota: UserQuota | None = None,
        team_quota: TeamQuota | None = None,
        team_daily: int = 0,
        team_tokens: int = 0,
        team_storage: int = 0,
        user_tokens: int = 0,
        active_user: int = 0,
        active_team: int = 0,
    ) -> None:
        self._user_quota = user_quota
        self._team_quota = team_quota
        self._team_daily = team_daily
        self._team_tokens = team_tokens
        self._team_storage = team_storage
        self._user_tokens = user_tokens
        self._active_user = active_user
        self._active_team = active_team

    async def get_for_user(self, user_id: str) -> UserQuota | None:
        return self._user_quota

    async def get_for_team(self, team_id: str) -> TeamQuota | None:
        return self._team_quota

    async def team_daily_session_count(self, team_id: str, since) -> int:
        return self._team_daily

    async def team_monthly_token_sum(self, team_id: str, since) -> int:
        return self._team_tokens

    async def team_storage_bytes(self, team_id: str) -> int:
        return self._team_storage

    async def user_monthly_token_sum(self, user_id: str, since) -> int:
        return self._user_tokens

    async def count_active_sessions(self, *, user_id=None, team_id=None) -> int:
        return self._active_team if team_id is not None else self._active_user


class _FakeUoW:
    def __init__(self, quota: _FakeQuotaRepo) -> None:
        self.quota = quota

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> None:
        return None


class _FakeUsageQuery:
    def __init__(
        self,
        *,
        daily_sessions: int = 0,
        monthly_tokens: int = 0,
        storage_bytes: int = 0,
    ) -> None:
        self._snapshot = QuotaUsageSnapshot(
            daily_sessions=daily_sessions,
            monthly_tokens=monthly_tokens,
            storage_bytes=storage_bytes,
        )

    async def snapshot(self, *, user_id, session_since, token_since) -> QuotaUsageSnapshot:
        return self._snapshot


def _service(repo: _FakeQuotaRepo, usage: _FakeUsageQuery) -> QuotaService:
    return QuotaService(uow_factory=lambda: _FakeUoW(repo), usage_query=usage)


# --- per-user ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_quota_configured_allows() -> None:
    service = _service(_FakeQuotaRepo(), _FakeUsageQuery())
    await service.check_session_quota("u1")


@pytest.mark.asyncio
async def test_user_daily_session_limit_rejects() -> None:
    repo = _FakeQuotaRepo(user_quota=UserQuota(user_id="u1", daily_session_limit=2))
    service = _service(repo, _FakeUsageQuery(daily_sessions=2))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1")
    assert exc.value.error_key == "errors.quota.dailySessionLimit"
    assert exc.value.error_params == {"scope": "user"}


@pytest.mark.asyncio
async def test_user_monthly_token_limit_rejects() -> None:
    repo = _FakeQuotaRepo(user_quota=UserQuota(user_id="u1", monthly_token_limit=100))
    service = _service(repo, _FakeUsageQuery(monthly_tokens=100))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1")
    assert exc.value.error_key == "errors.quota.monthlyTokenLimit"


@pytest.mark.asyncio
async def test_user_under_all_limits_allows() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(
            user_id="u1",
            daily_session_limit=5,
            monthly_token_limit=1000,
            max_concurrent_tasks=3,
        ),
        active_user=1,
    )
    service = _service(repo, _FakeUsageQuery(daily_sessions=1, monthly_tokens=10))
    await service.check_session_quota("u1")


# --- max_concurrent_tasks ---------------------------------------------------


@pytest.mark.asyncio
async def test_user_concurrency_limit_rejects() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", max_concurrent_tasks=1),
        active_user=1,
    )
    service = _service(repo, _FakeUsageQuery())
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1")
    assert exc.value.error_key == "errors.quota.maxConcurrentTasks"
    assert exc.value.error_params == {"scope": "user"}


@pytest.mark.asyncio
async def test_team_concurrency_limit_rejects() -> None:
    repo = _FakeQuotaRepo(
        team_quota=TeamQuota(team_id="t1", max_concurrent_tasks=2),
        active_team=2,
    )
    service = _service(repo, _FakeUsageQuery())
    scope = OwnerScope.team("u1", "t1")
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1", scope=scope)
    assert exc.value.error_key == "errors.quota.maxConcurrentTasks"
    assert exc.value.error_params == {"scope": "team"}


# --- per-team / 更严者 -------------------------------------------------------


@pytest.mark.asyncio
async def test_team_daily_limit_rejects_when_user_within() -> None:
    # 用户额度宽松但团队日会话已满 → 团队维度拒绝（团队更严）。
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", daily_session_limit=100),
        team_quota=TeamQuota(team_id="t1", daily_session_limit=3),
        team_daily=3,
    )
    service = _service(repo, _FakeUsageQuery(daily_sessions=1))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1", scope=OwnerScope.team("u1", "t1"))
    assert exc.value.error_params == {"scope": "team"}


@pytest.mark.asyncio
async def test_user_limit_rejects_first_when_stricter_than_team() -> None:
    # 用户维度先超限（用户更严）→ 用户维度拒绝。
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", daily_session_limit=2),
        team_quota=TeamQuota(team_id="t1", daily_session_limit=100),
        team_daily=1,
    )
    service = _service(repo, _FakeUsageQuery(daily_sessions=2))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_session_quota("u1", scope=OwnerScope.team("u1", "t1"))
    assert exc.value.error_params == {"scope": "user"}


@pytest.mark.asyncio
async def test_team_scope_within_both_allows() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", daily_session_limit=10, monthly_token_limit=1000),
        team_quota=TeamQuota(team_id="t1", daily_session_limit=50, monthly_token_limit=9000),
        team_daily=5,
        team_tokens=100,
    )
    service = _service(repo, _FakeUsageQuery(daily_sessions=1, monthly_tokens=10))
    await service.check_session_quota("u1", scope=OwnerScope.team("u1", "t1"))


@pytest.mark.asyncio
async def test_personal_scope_ignores_team_quota() -> None:
    # 个人 scope 即便存在团队配额也不应触发（个人 scope 走用户维度）。
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", daily_session_limit=10),
        team_quota=TeamQuota(team_id="t1", daily_session_limit=1),
        team_daily=99,
    )
    service = _service(repo, _FakeUsageQuery(daily_sessions=1))
    await service.check_session_quota("u1", scope=OwnerScope.personal("u1"))


# --- 边界 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_at_limit_rejects_one_below_allows() -> None:
    quota = UserQuota(user_id="u1", daily_session_limit=3)
    # 恰好等于上限 → 拒绝（>= 语义）。
    at_limit = _service(_FakeQuotaRepo(user_quota=quota), _FakeUsageQuery(daily_sessions=3))
    with pytest.raises(TooManyRequestsError):
        await at_limit.check_session_quota("u1")
    # 低于上限一位 → 放行。
    below = _service(_FakeQuotaRepo(user_quota=quota), _FakeUsageQuery(daily_sessions=2))
    await below.check_session_quota("u1")


# --- storage ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_storage_limit_rejects_with_incoming() -> None:
    repo = _FakeQuotaRepo(user_quota=UserQuota(user_id="u1", max_storage_bytes=1000))
    service = _service(repo, _FakeUsageQuery(storage_bytes=900))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_storage_quota("u1", incoming_bytes=200)
    assert exc.value.error_key == "errors.quota.storageLimit"
    assert exc.value.error_params == {"scope": "user"}


@pytest.mark.asyncio
async def test_team_storage_limit_rejects() -> None:
    repo = _FakeQuotaRepo(
        team_quota=TeamQuota(team_id="t1", max_storage_bytes=5000),
        team_storage=4900,
    )
    service = _service(repo, _FakeUsageQuery(storage_bytes=0))
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_storage_quota(
            "u1", incoming_bytes=200, scope=OwnerScope.team("u1", "t1")
        )
    assert exc.value.error_params == {"scope": "team"}


@pytest.mark.asyncio
async def test_storage_within_limit_allows() -> None:
    repo = _FakeQuotaRepo(user_quota=UserQuota(user_id="u1", max_storage_bytes=1000))
    service = _service(repo, _FakeUsageQuery(storage_bytes=100))
    await service.check_storage_quota("u1", incoming_bytes=200)


# --- 运行中 Token 拦截（check_model_call_budget）----------------------------


@pytest.mark.asyncio
async def test_model_call_budget_user_over_rejects() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", monthly_token_limit=500),
        user_tokens=500,
    )
    service = _service(repo, _FakeUsageQuery())
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_model_call_budget(user_id="u1")
    assert exc.value.error_key == "errors.quota.monthlyTokenLimit"
    assert exc.value.error_params == {"scope": "user"}


@pytest.mark.asyncio
async def test_model_call_budget_team_over_rejects() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", monthly_token_limit=100000),
        user_tokens=1,
        team_quota=TeamQuota(team_id="t1", monthly_token_limit=800),
        team_tokens=800,
    )
    service = _service(repo, _FakeUsageQuery())
    with pytest.raises(TooManyRequestsError) as exc:
        await service.check_model_call_budget(user_id="u1", team_id="t1")
    assert exc.value.error_params == {"scope": "team"}


@pytest.mark.asyncio
async def test_model_call_budget_under_allows() -> None:
    repo = _FakeQuotaRepo(
        user_quota=UserQuota(user_id="u1", monthly_token_limit=500),
        user_tokens=100,
    )
    service = _service(repo, _FakeUsageQuery())
    await service.check_model_call_budget(user_id="u1")


@pytest.mark.asyncio
async def test_model_call_budget_no_quota_allows() -> None:
    service = _service(_FakeQuotaRepo(), _FakeUsageQuery())
    await service.check_model_call_budget(user_id="u1", team_id="t1")
