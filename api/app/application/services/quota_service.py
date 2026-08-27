from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.ports.queries import QuotaUsageQueryPort
from app.domain.errors import TooManyRequestsError
from app.domain.repositories.uow import IUnitOfWork


class QuotaService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        usage_query: QuotaUsageQueryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._usage_query = usage_query

    async def check_session_quota(self, user_id: str) -> None:
        async with self._uow_factory() as uow:
            quota = await uow.quota.get_for_user(user_id)
        if not quota:
            return
        now = datetime.now(UTC)
        usage = await self._usage_query.snapshot(
            user_id=user_id,
            session_since=now - timedelta(days=1),
            token_since=now - timedelta(days=30),
        )
        if (
            quota.daily_session_limit is not None
            and usage.daily_sessions >= quota.daily_session_limit
        ):
            raise TooManyRequestsError("已达到每日会话上限")
        if (
            quota.monthly_token_limit is not None
            and usage.monthly_tokens >= quota.monthly_token_limit
        ):
            raise TooManyRequestsError("已达到月度 Token 上限")

    async def check_storage_quota(self, user_id: str, incoming_bytes: int = 0) -> None:
        async with self._uow_factory() as uow:
            quota = await uow.quota.get_for_user(user_id)
        if not quota or quota.max_storage_bytes is None:
            return
        now = datetime.now(UTC)
        usage = await self._usage_query.snapshot(
            user_id=user_id,
            session_since=now - timedelta(days=1),
            token_since=now - timedelta(days=30),
        )
        if usage.storage_bytes + incoming_bytes > quota.max_storage_bytes:
            raise TooManyRequestsError("已达到存储容量上限")
