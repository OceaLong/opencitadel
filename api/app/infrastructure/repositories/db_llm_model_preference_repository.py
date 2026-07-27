#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.llm_model_preference_repository import (
    LLMModelPreferenceRepository,
)
from app.infrastructure.models.llm_model_preference import LLMModelPreferenceORM


def _preference_identity(
    scope: Optional[OwnerScope],
) -> tuple[str, str, Optional[str], Optional[str]]:
    if scope is None:
        return "global", "global", None, None
    if scope.type == OwnerScopeType.TEAM:
        if not scope.team_id:
            raise ValueError("团队模型偏好缺少 team_id")
        return f"team:{scope.team_id}", "team", None, scope.team_id
    return f"user:{scope.user_id}", "user", scope.user_id, None


class DBLLMModelPreferenceRepository(LLMModelPreferenceRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_model_id(self, scope: Optional[OwnerScope]) -> Optional[str]:
        preference_id, _, _, _ = _preference_identity(scope)
        result = await self.db_session.execute(
            select(LLMModelPreferenceORM.model_id).where(
                LLMModelPreferenceORM.id == preference_id
            )
        )
        return result.scalar_one_or_none()

    async def set_model_id(
        self,
        scope: Optional[OwnerScope],
        model_id: str,
    ) -> None:
        preference_id, scope_type, owner_user_id, team_id = _preference_identity(scope)
        result = await self.db_session.execute(
            select(LLMModelPreferenceORM).where(
                LLMModelPreferenceORM.id == preference_id
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(
                LLMModelPreferenceORM(
                    id=preference_id,
                    scope_type=scope_type,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    model_id=model_id,
                )
            )
            return
        record.model_id = model_id
        record.updated_at = datetime.now()
