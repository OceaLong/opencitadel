#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.questionnaire import Questionnaire, QuestionnaireResponse
from app.domain.repositories.questionnaire_repository import QuestionnaireRepository
from app.infrastructure.models.questionnaire import QuestionnaireModel, QuestionnaireResponseModel


class DBQuestionnaireRepository(QuestionnaireRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, questionnaire: Questionnaire) -> None:
        stmt = select(QuestionnaireModel).where(QuestionnaireModel.id == questionnaire.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(QuestionnaireModel.from_domain(questionnaire))
            return
        record.title = questionnaire.title
        record.description = questionnaire.description
        record.questions = questionnaire.questions
        record.status = questionnaire.status.value
        record.slug = questionnaire.slug
        record.updated_at = questionnaire.updated_at

    async def get_by_id(self, questionnaire_id: str) -> Optional[Questionnaire]:
        stmt = select(QuestionnaireModel).where(QuestionnaireModel.id == questionnaire_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_slug(self, slug: str) -> Optional[Questionnaire]:
        stmt = select(QuestionnaireModel).where(QuestionnaireModel.slug == slug)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save_response(self, response: QuestionnaireResponse) -> None:
        self.db_session.add(QuestionnaireResponseModel.from_domain(response))

    async def count_responses(self, questionnaire_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(QuestionnaireResponseModel)
            .where(QuestionnaireResponseModel.questionnaire_id == questionnaire_id)
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_responses(
            self,
            questionnaire_id: str,
            *,
            limit: int = 5000,
    ) -> List[QuestionnaireResponse]:
        stmt = (
            select(QuestionnaireResponseModel)
            .where(QuestionnaireResponseModel.questionnaire_id == questionnaire_id)
            .order_by(QuestionnaireResponseModel.created_at.desc())
            .limit(max(1, min(limit, 5000)))
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]
