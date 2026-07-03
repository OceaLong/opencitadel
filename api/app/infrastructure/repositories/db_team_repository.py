#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.repositories.team_repository import TeamRepository
from app.infrastructure.models.team import TeamMemberORM, TeamORM


class DBTeamRepository(TeamRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_id(self, team_id: str) -> Optional[Team]:
        record = await self.db_session.get(TeamORM, team_id)
        return record.to_domain() if record else None

    async def list_for_user(self, user_id: str) -> List[Team]:
        stmt = (
            select(TeamORM)
            .join(TeamMemberORM, TeamMemberORM.team_id == TeamORM.id)
            .where(TeamMemberORM.user_id == user_id)
            .order_by(TeamORM.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Team]:
        stmt = select(TeamORM).order_by(TeamORM.created_at.desc()).limit(limit).offset(offset)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(TeamORM))
        return int(result.scalar_one() or 0)

    async def count_members(self, team_id: str) -> int:
        result = await self.db_session.execute(
            select(func.count())
            .select_from(TeamMemberORM)
            .where(TeamMemberORM.team_id == team_id),
        )
        return int(result.scalar_one() or 0)

    async def count_members_by_teams(self, team_ids: List[str]) -> dict[str, int]:
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

    async def get_member(self, team_id: str, user_id: str) -> Optional[TeamMember]:
        record = await self.db_session.get(TeamMemberORM, {"team_id": team_id, "user_id": user_id})
        return record.to_domain() if record else None

    async def list_members(self, team_id: str) -> List[TeamMember]:
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
