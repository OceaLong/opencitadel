#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.fortune_prediction import FortunePrediction
from app.domain.repositories.fortune_prediction_repository import FortunePredictionRepository
from app.infrastructure.models.fortune_prediction import FortunePredictionModel


class DBFortunePredictionRepository(FortunePredictionRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, prediction: FortunePrediction) -> None:
        self.db_session.add(FortunePredictionModel.from_domain(prediction))

    async def get_by_share_id(self, share_id: str) -> Optional[FortunePrediction]:
        stmt = select(FortunePredictionModel).where(FortunePredictionModel.share_id == share_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None
