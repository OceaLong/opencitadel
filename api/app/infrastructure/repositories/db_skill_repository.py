from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.skill import Skill
from app.domain.repositories.skill_repository import SkillRepository
from app.infrastructure.models.skill import SkillORM


class DBSkillRepository(SkillRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: OwnerScope | None, *, global_only: bool = False):
        if global_only:
            # Safe default for unauthenticated/public surfaces (e.g. the A2A
            # agent-card discovery endpoint): expose only globally-visible
            # Skills, never tenant-private/team ones, regardless of scope.
            return stmt.where(SkillORM.visibility == "global")
        if scope is None:
            # WARNING: scope=None returns every tenant's rows unfiltered. This is
            # only safe for trusted internal callers (e.g. startup seeding).
            # Never reach this branch from an anonymous/unauthenticated request —
            # pass an explicit scope or global_only=True instead.
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            owner_filter = SkillORM.team_id == scope.team_id
        else:
            owner_filter = (SkillORM.owner_user_id == scope.user_id) & SkillORM.team_id.is_(None)
        return stmt.where(or_(SkillORM.visibility == "global", owner_filter))

    async def get_all(
        self,
        enabled_only: bool = False,
        scope: OwnerScope | None = None,
        *,
        global_only: bool = False,
    ) -> list[Skill]:
        stmt = self._apply_scope(select(SkillORM), scope, global_only=global_only).order_by(
            SkillORM.category, SkillORM.name
        )
        if enabled_only:
            stmt = stmt.where(SkillORM.enabled.is_(True))
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_by_id(self, skill_id: str, scope: OwnerScope | None = None) -> Skill | None:
        stmt = self._apply_scope(select(SkillORM).where(SkillORM.id == skill_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_slug(self, slug: str) -> Skill | None:
        stmt = select(SkillORM).where(SkillORM.slug == slug)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, skill: Skill) -> None:
        stmt = select(SkillORM).where(SkillORM.id == skill.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        skill.updated_at = datetime.now(UTC)
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
            record.team_id = skill.team_id
            record.visibility = (
                skill.visibility.value if hasattr(skill.visibility, "value") else skill.visibility
            )
            record.updated_at = skill.updated_at
        else:
            self.db_session.add(SkillORM.from_domain(skill))

    async def delete_by_id(self, skill_id: str) -> None:
        await self.db_session.execute(delete(SkillORM).where(SkillORM.id == skill_id))

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(SkillORM))
        return result.scalar() or 0
