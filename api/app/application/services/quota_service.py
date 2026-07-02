#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import func, select

from app.application.errors.exceptions import TooManyRequestsError
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.file import FileModel
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM
from app.infrastructure.models.session import SessionModel


class QuotaService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def check_session_quota(self, user_id: str) -> None:
        async with self._uow_factory() as uow:
            quota = await uow.quota.get_for_user(user_id)
            if not quota:
                return
            if quota.daily_session_limit is not None:
                since = datetime.now() - timedelta(days=1)
                result = await uow.db_session.execute(  # type: ignore[attr-defined]
                    select(func.count(SessionModel.id)).where(
                        SessionModel.owner_user_id == user_id,
                        SessionModel.created_at >= since,
                    )
                )
                if int(result.scalar() or 0) >= quota.daily_session_limit:
                    raise TooManyRequestsError("已达到每日会话上限")
            if quota.monthly_token_limit is not None:
                since = datetime.now() - timedelta(days=30)
                result = await uow.db_session.execute(  # type: ignore[attr-defined]
                    select(func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0)).where(
                        LLMTokenUsageORM.owner_user_id == user_id,
                        LLMTokenUsageORM.created_at >= since,
                    )
                )
                if int(result.scalar() or 0) >= quota.monthly_token_limit:
                    raise TooManyRequestsError("已达到月度 Token 上限")

    async def check_storage_quota(self, user_id: str, incoming_bytes: int = 0) -> None:
        async with self._uow_factory() as uow:
            quota = await uow.quota.get_for_user(user_id)
            if not quota or quota.max_storage_bytes is None:
                return
            result = await uow.db_session.execute(  # type: ignore[attr-defined]
                select(func.coalesce(func.sum(FileModel.size), 0)).where(FileModel.owner_user_id == user_id)
            )
            if int(result.scalar() or 0) + incoming_bytes > quota.max_storage_bytes:
                raise TooManyRequestsError("已达到存储容量上限")
