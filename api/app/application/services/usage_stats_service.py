#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import func, select

from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM


class UsageStatsService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def aggregate_usage(
            self,
            *,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
    ) -> dict:
        async with self._uow_factory() as uow:
            stmt = select(
                func.coalesce(func.sum(LLMTokenUsageORM.prompt_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.completion_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0),
                func.count(LLMTokenUsageORM.id),
            )
            if owner_user_id:
                stmt = stmt.where(LLMTokenUsageORM.owner_user_id == owner_user_id)
            if team_id:
                stmt = stmt.where(LLMTokenUsageORM.team_id == team_id)
            if start_at:
                stmt = stmt.where(LLMTokenUsageORM.created_at >= start_at)
            if end_at:
                stmt = stmt.where(LLMTokenUsageORM.created_at <= end_at)
            result = await uow.db_session.execute(stmt)  # type: ignore[attr-defined]
            prompt, completion, total, count = result.one()
        return {
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(total or 0),
            "call_count": int(count or 0),
        }
