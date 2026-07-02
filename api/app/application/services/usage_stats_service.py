#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Callable, Literal, Optional

from sqlalchemy import desc, func, select

from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM

UsageBreakdownDimension = Literal["model", "user", "team", "agent"]


class UsageStatsService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def _apply_filters(
            self,
            stmt,
            *,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
    ):
        if owner_user_id:
            stmt = stmt.where(LLMTokenUsageORM.owner_user_id == owner_user_id)
        if team_id:
            stmt = stmt.where(LLMTokenUsageORM.team_id == team_id)
        if start_at:
            stmt = stmt.where(LLMTokenUsageORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(LLMTokenUsageORM.created_at <= end_at)
        return stmt

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
                func.coalesce(func.sum(LLMTokenUsageORM.cached_tokens), 0),
                func.count(LLMTokenUsageORM.id),
            )
            stmt = self._apply_filters(
                stmt,
                owner_user_id=owner_user_id,
                team_id=team_id,
                start_at=start_at,
                end_at=end_at,
            )
            result = await uow.db_session.execute(stmt)  # type: ignore[attr-defined]
            prompt, completion, total, cached, count = result.one()
        return {
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(total or 0),
            "cached_tokens": int(cached or 0),
            "call_count": int(count or 0),
        }

    async def usage_timeseries(
            self,
            *,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
    ) -> list[dict]:
        day_bucket = func.date(LLMTokenUsageORM.created_at).label("date")
        async with self._uow_factory() as uow:
            stmt = select(
                day_bucket,
                func.coalesce(func.sum(LLMTokenUsageORM.prompt_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.completion_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.cached_tokens), 0),
                func.count(LLMTokenUsageORM.id),
            ).group_by(day_bucket).order_by(day_bucket)
            stmt = self._apply_filters(
                stmt,
                owner_user_id=owner_user_id,
                team_id=team_id,
                start_at=start_at,
                end_at=end_at,
            )
            result = await uow.db_session.execute(stmt)  # type: ignore[attr-defined]
            rows = result.all()
        return [
            {
                "date": str(day),
                "prompt_tokens": int(prompt or 0),
                "completion_tokens": int(completion or 0),
                "total_tokens": int(total or 0),
                "cached_tokens": int(cached or 0),
                "call_count": int(count or 0),
            }
            for day, prompt, completion, total, cached, count in rows
        ]

    async def usage_breakdown(
            self,
            *,
            dimension: UsageBreakdownDimension,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
            start_at: Optional[datetime] = None,
            end_at: Optional[datetime] = None,
            limit: int = 10,
    ) -> list[dict]:
        if dimension == "model":
            key_expr = func.coalesce(LLMTokenUsageORM.model_name, "unknown")
        elif dimension == "user":
            key_expr = func.coalesce(LLMTokenUsageORM.owner_user_id, "unknown")
        elif dimension == "team":
            key_expr = func.coalesce(LLMTokenUsageORM.team_id, "personal")
        else:
            key_expr = func.coalesce(LLMTokenUsageORM.agent, "unknown")

        key_label = key_expr.label("key")
        total_tokens = func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0).label("total_tokens")
        call_count = func.count(LLMTokenUsageORM.id).label("call_count")

        async with self._uow_factory() as uow:
            stmt = (
                select(key_label, total_tokens, call_count)
                .group_by(key_label)
                .order_by(desc(total_tokens))
                .limit(max(1, min(limit, 50)))
            )
            stmt = self._apply_filters(
                stmt,
                owner_user_id=owner_user_id,
                team_id=team_id,
                start_at=start_at,
                end_at=end_at,
            )
            result = await uow.db_session.execute(stmt)  # type: ignore[attr-defined]
            rows = result.all()
        return [
            {
                "key": str(key),
                "total_tokens": int(tokens or 0),
                "call_count": int(calls or 0),
            }
            for key, tokens, calls in rows
        ]
