#!/usr/bin/env python
# -*- coding: utf-8 -*-
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.llm_token_usage import LLMTokenUsage, SessionTokenUsageSummary
from app.domain.repositories.llm_token_usage_repository import LLMTokenUsageRepository
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM


class DBLLMTokenUsageRepository(LLMTokenUsageRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, usage: LLMTokenUsage) -> None:
        self.db_session.add(LLMTokenUsageORM.from_domain(usage))

    async def save_many(self, usages: list[LLMTokenUsage]) -> None:
        if not usages:
            return
        self.db_session.add_all([LLMTokenUsageORM.from_domain(usage) for usage in usages])

    async def list_by_session(self, session_id: str) -> list[LLMTokenUsage]:
        stmt = (
            select(LLMTokenUsageORM)
            .where(LLMTokenUsageORM.session_id == session_id)
            .order_by(LLMTokenUsageORM.created_at.asc())
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def aggregate_by_session(self, session_id: str) -> SessionTokenUsageSummary:
        stmt = select(
            func.coalesce(func.sum(LLMTokenUsageORM.prompt_tokens), 0),
            func.coalesce(func.sum(LLMTokenUsageORM.completion_tokens), 0),
            func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0),
            func.count(LLMTokenUsageORM.id),
        ).where(LLMTokenUsageORM.session_id == session_id)
        result = await self.db_session.execute(stmt)
        prompt, completion, total, count = result.one()
        return SessionTokenUsageSummary(
            session_id=session_id,
            prompt_tokens=int(prompt or 0),
            completion_tokens=int(completion or 0),
            total_tokens=int(total or 0),
            call_count=int(count or 0),
        )
