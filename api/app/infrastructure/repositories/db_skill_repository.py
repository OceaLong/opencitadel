#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_, select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.skill import Skill
from app.domain.models.scope import OwnerScope
from app.domain.repositories.skill_repository import SkillRepository
from app.infrastructure.models.skill import SkillORM


class DBSkillRepository(SkillRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        if scope is None:
            return stmt
        owner_filter = SkillORM.owner_user_id == scope.user_id
        return stmt.where(or_(SkillORM.visibility == "global", owner_filter))

    async def get_all(self, enabled_only: bool = False, scope: Optional[OwnerScope] = None) -> List[Skill]:
        stmt = self._apply_scope(select(SkillORM), scope).order_by(SkillORM.category, SkillORM.name)
        if enabled_only:
            stmt = stmt.where(SkillORM.enabled.is_(True))
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_by_id(self, skill_id: str, scope: Optional[OwnerScope] = None) -> Optional[Skill]:
        stmt = self._apply_scope(select(SkillORM).where(SkillORM.id == skill_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_slug(self, slug: str) -> Optional[Skill]:
        stmt = select(SkillORM).where(SkillORM.slug == slug)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, skill: Skill) -> None:
        stmt = select(SkillORM).where(SkillORM.id == skill.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        skill.updated_at = datetime.now()
        if record:
            record.name = skill.name
            record.slug = skill.slug
            record.description = skill.description
            record.icon = skill.icon
            record.category = skill.category
            record.system_prompt = skill.system_prompt
            record.allowed_tools = skill.allowed_tools
            record.recommended_model_id = skill.recommended_model_id
            record.agent_params = skill.agent_params.model_dump()
            record.examples = skill.examples
            record.enabled = skill.enabled
            record.owner_user_id = skill.owner_user_id
            record.visibility = skill.visibility.value if hasattr(skill.visibility, "value") else skill.visibility
            record.updated_at = skill.updated_at
        else:
            self.db_session.add(SkillORM.from_domain(skill))

    async def delete_by_id(self, skill_id: str) -> None:
        await self.db_session.execute(delete(SkillORM).where(SkillORM.id == skill_id))

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(SkillORM))
        return result.scalar() or 0
