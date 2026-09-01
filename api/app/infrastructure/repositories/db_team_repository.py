from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.repositories.team_repository import TeamRepository
from app.infrastructure.models.team import TeamMemberORM, TeamORM

# Team-scoped resource tables carrying both team_id and owner_user_id. Kept in
# sync with db_user_repository._OWNED_RESOURCE_TABLES so team teardown and
# per-user teardown reason over the same resource surface.
_TEAM_RESOURCE_TABLES = (
    "scheduled_jobs",
    "skills",
    "mcp_servers",
    "a2a_servers",
    "inference_models",
    "inference_endpoints",
    "inference_bindings",
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "files",
    "llm_token_usages",
)


class DBTeamRepository(TeamRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_id(self, team_id: str) -> Team | None:
        record = await self.db_session.get(TeamORM, team_id)
        return record.to_domain() if record else None

    async def list_for_user(self, user_id: str) -> list[Team]:
        stmt = (
            select(TeamORM)
            .join(TeamMemberORM, TeamMemberORM.team_id == TeamORM.id)
            .where(TeamMemberORM.user_id == user_id)
            .order_by(TeamORM.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[Team]:
        stmt = select(TeamORM).order_by(TeamORM.created_at.desc()).limit(limit).offset(offset)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(TeamORM))
        return int(result.scalar_one() or 0)

    async def count_members(self, team_id: str) -> int:
        result = await self.db_session.execute(
            select(func.count()).select_from(TeamMemberORM).where(TeamMemberORM.team_id == team_id),
        )
        return int(result.scalar_one() or 0)

    async def count_members_by_teams(self, team_ids: list[str]) -> dict[str, int]:
        if not team_ids:
            return {}
        result = await self.db_session.execute(
            select(TeamMemberORM.team_id, func.count())
            .where(TeamMemberORM.team_id.in_(team_ids))
            .group_by(TeamMemberORM.team_id),
        )
        return {team_id: int(count) for team_id, count in result.all()}

    async def save(self, team: Team) -> None:
        record = await self.db_session.get(TeamORM, team.id)
        if record:
            record.update_from_domain(team)
        else:
            self.db_session.add(TeamORM.from_domain(team))
        await self.db_session.flush()

    async def delete_by_id(self, team_id: str) -> None:
        await self.db_session.execute(delete(TeamORM).where(TeamORM.id == team_id))

    async def transfer_resources_to_owner(self, team_id: str, owner_user_id: str) -> int:
        moved = 0
        for table in _TEAM_RESOURCE_TABLES:
            result = await self.db_session.execute(
                text(
                    f"UPDATE {table} SET team_id = NULL, owner_user_id = :owner_user_id "
                    "WHERE team_id = :team_id"
                ),
                {"owner_user_id": owner_user_id, "team_id": team_id},
            )
            moved += result.rowcount or 0
        return moved

    async def delete_resources(self, team_id: str) -> int:
        removed = 0
        for table in _TEAM_RESOURCE_TABLES:
            result = await self.db_session.execute(
                text(f"DELETE FROM {table} WHERE team_id = :team_id"),
                {"team_id": team_id},
            )
            removed += result.rowcount or 0
        return removed

    async def get_member(self, team_id: str, user_id: str) -> TeamMember | None:
        record = await self.db_session.get(TeamMemberORM, {"team_id": team_id, "user_id": user_id})
        return record.to_domain() if record else None

    async def list_members(self, team_id: str) -> list[TeamMember]:
        stmt = select(TeamMemberORM).where(TeamMemberORM.team_id == team_id)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def add_member(self, member: TeamMember) -> None:
        record = await self.db_session.get(
            TeamMemberORM,
            {"team_id": member.team_id, "user_id": member.user_id},
        )
        if record:
            record.role = member.role.value
        else:
            self.db_session.add(TeamMemberORM.from_domain(member))

    async def update_member_role(self, team_id: str, user_id: str, role: TeamRole) -> None:
        await self.db_session.execute(
            update(TeamMemberORM)
            .where(TeamMemberORM.team_id == team_id, TeamMemberORM.user_id == user_id)
            .values(role=role.value)
        )

    async def remove_member(self, team_id: str, user_id: str) -> None:
        await self.db_session.execute(
            delete(TeamMemberORM).where(
                TeamMemberORM.team_id == team_id,
                TeamMemberORM.user_id == user_id,
            )
        )
