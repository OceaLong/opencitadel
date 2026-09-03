"""Quota admission is enforced at the service layer for every session path.

Historically ``check_session_quota`` lived only in the HTTP route, so A2A,
scheduled jobs, KB chat, and patrol runs created sessions without any
daily/concurrency/token admission. These tests pin the sunk-in enforcement:
``SessionService.create_session`` checks quota for scoped creations (with an
explicit ``quota_exempt`` escape hatch), and ``QuotaService`` accepts an
ownerless (``user_id=None``) team path without touching user quotas.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.quota_service import QuotaService
from app.application.services.session_service import SessionService
from app.domain.errors import TooManyRequestsError
from app.domain.models.scope import OwnerScope


def _uow() -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.save = AsyncMock()
    return uow


def _service(quota_service) -> SessionService:
    return SessionService(
        uow_factory=lambda: _uow(),
        sandbox_factory=MagicMock(),
        run_projection=AsyncMock(),
        session_list_publisher=AsyncMock(),
        quota_service=quota_service,
    )


def test_create_session_enforces_quota_for_scoped_creation() -> None:
    quota = MagicMock()
    quota.check_session_quota = AsyncMock(side_effect=TooManyRequestsError("已达到每日会话上限"))
    service = _service(quota)
    scope = OwnerScope.personal("u1")

    with pytest.raises(TooManyRequestsError):
        asyncio.run(service.create_session(scope=scope))

    quota.check_session_quota.assert_awaited_once_with("u1", scope=scope)


def test_create_session_quota_exempt_skips_check() -> None:
    quota = MagicMock()
    quota.check_session_quota = AsyncMock(side_effect=TooManyRequestsError("已达到每日会话上限"))
    service = _service(quota)

    session = asyncio.run(
        service.create_session(scope=OwnerScope.personal("u1"), quota_exempt=True)
    )

    assert session is not None
    quota.check_session_quota.assert_not_awaited()


def test_quota_service_skips_user_dimension_for_ownerless_path() -> None:
    """Team-only callers (e.g. an ownerless patrol pack) pass user_id=None."""
    uow = _uow()
    uow.quota.get_for_user = AsyncMock()
    uow.quota.get_for_team = AsyncMock(return_value=None)
    usage_query = MagicMock()
    usage_query.snapshot = AsyncMock()
    service = QuotaService(uow_factory=lambda: uow, usage_query=usage_query)

    asyncio.run(service.check_session_quota(None, scope=OwnerScope.team("u1", "t1")))

    uow.quota.get_for_user.assert_not_awaited()
    usage_query.snapshot.assert_not_awaited()
    uow.quota.get_for_team.assert_awaited_once_with("t1")


def test_quota_service_still_enforces_team_limits_for_ownerless_path() -> None:
    uow = _uow()
    team_quota = MagicMock()
    team_quota.daily_session_limit = 1
    team_quota.monthly_token_limit = None
    team_quota.max_concurrent_tasks = None
    uow.quota.get_for_team = AsyncMock(return_value=team_quota)
    uow.quota.team_daily_session_count = AsyncMock(return_value=1)
    uow.quota.team_monthly_token_sum = AsyncMock(return_value=0)
    usage_query = MagicMock()
    usage_query.snapshot = AsyncMock()
    service = QuotaService(uow_factory=lambda: uow, usage_query=usage_query)

    with pytest.raises(TooManyRequestsError):
        asyncio.run(service.check_session_quota(None, scope=OwnerScope.team("u1", "t1")))


def test_quota_check_reuses_caller_transaction() -> None:
    """Passing the caller's open write UoW must not open a second one: the
    infrastructure nesting guard raises NestedUnitOfWorkError otherwise (the
    patrol trigger 500'd on exactly this)."""
    outer = _uow()
    outer.quota.get_for_user = AsyncMock(return_value=None)
    outer.quota.get_for_team = AsyncMock(return_value=None)

    def _factory():
        raise AssertionError("must reuse the provided uow, not open a new one")

    service = QuotaService(uow_factory=_factory, usage_query=MagicMock())

    asyncio.run(service.check_session_quota("u1", scope=OwnerScope.team("u1", "t1"), uow=outer))

    outer.quota.get_for_user.assert_awaited_once()
    outer.__aenter__.assert_not_awaited()
