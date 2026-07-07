#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.invitation import Invitation, InvitationType
from app.domain.repositories.invitation_repository import InvitationRepository
from app.infrastructure.models.invitation import InvitationORM


class DBInvitationRepository(InvitationRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_token(self, token: str) -> Optional[Invitation]:
        result = await self.db_session.execute(select(InvitationORM).where(InvitationORM.token == token))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_pending_team_invitation(self, team_id: str, email: str) -> Optional[Invitation]:
        normalized_email = email.strip().lower()
        now = datetime.now()
        result = await self.db_session.execute(
            select(InvitationORM).where(
                InvitationORM.type == InvitationType.TEAM.value,
                InvitationORM.team_id == team_id,
                InvitationORM.email == normalized_email,
                InvitationORM.accepted_at.is_(None),
                InvitationORM.expires_at > now,
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list(self, invitation_type: InvitationType | None = None, limit: int = 100, offset: int = 0) -> List[Invitation]:
        stmt = select(InvitationORM).order_by(InvitationORM.created_at.desc())
        if invitation_type is not None:
            stmt = stmt.where(InvitationORM.type == invitation_type.value)
        stmt = stmt.offset(max(offset, 0)).limit(max(1, min(limit, 500)))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(self, invitation_type: InvitationType | None = None) -> int:
        stmt = select(func.count()).select_from(InvitationORM)
        if invitation_type is not None:
            stmt = stmt.where(InvitationORM.type == invitation_type.value)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def save(self, invitation: Invitation) -> None:
        record = await self.db_session.get(InvitationORM, invitation.id)
        if record:
            record.update_from_domain(invitation)
        else:
            self.db_session.add(InvitationORM.from_domain(invitation))

    async def delete_by_id(self, invitation_id: str) -> None:
        await self.db_session.execute(delete(InvitationORM).where(InvitationORM.id == invitation_id))
