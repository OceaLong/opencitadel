#!/usr/bin/env python
# -*- coding: utf-8 -*-
import secrets
from datetime import datetime, timedelta
from typing import Callable, List

from app.application.errors.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.repositories.uow import IUnitOfWork
from core.config import get_settings


class TeamService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def create_team(self, *, name: str, description: str, actor_user_id: str) -> Team:
        team = Team(name=name, description=description, created_by=actor_user_id)
        async with self._uow_factory() as uow:
            await uow.team.save(team)
            await uow.team.add_member(TeamMember(team_id=team.id, user_id=actor_user_id, role=TeamRole.OWNER))
        return team

    async def list_my_teams(self, user_id: str) -> List[Team]:
        async with self._uow_factory() as uow:
            return await uow.team.list_for_user(user_id)

    async def list_members(self, team_id: str, actor_user_id: str) -> List[TeamMember]:
        await self._require_team_admin(team_id, actor_user_id, allow_member=True)
        async with self._uow_factory() as uow:
            return await uow.team.list_members(team_id)

    async def create_team_invitation(self, *, team_id: str, actor_user_id: str, role: TeamRole) -> str:
        await self._require_team_admin(team_id, actor_user_id)
        token = secrets.token_urlsafe(32)
        invitation = Invitation(
            type=InvitationType.TEAM,
            team_id=team_id,
            team_role=role,
            token=token,
            invited_by=actor_user_id,
            expires_at=datetime.now() + timedelta(days=7),
        )
        async with self._uow_factory() as uow:
            await uow.invitation.save(invitation)
        return f"{get_settings().frontend_base_url.rstrip('/')}/invitations/{token}"

    async def accept_invitation(self, *, token: str, user_id: str) -> TeamMember:
        async with self._uow_factory() as uow:
            invitation = await uow.invitation.get_by_token(token)
            if not invitation or invitation.type != InvitationType.TEAM or not invitation.team_id:
                raise BadRequestError("邀请链接无效")
            if invitation.accepted:
                raise BadRequestError("邀请链接已被使用")
            if invitation.expires_at < datetime.now():
                raise BadRequestError("邀请链接已过期")
            member = TeamMember(
                team_id=invitation.team_id,
                user_id=user_id,
                role=invitation.team_role or TeamRole.MEMBER,
            )
            await uow.team.add_member(member)
            invitation.accepted_at = datetime.now()
            invitation.accepted_user_id = user_id
            await uow.invitation.save(invitation)
            return member

    async def _require_team_admin(self, team_id: str, user_id: str, *, allow_member: bool = False) -> TeamMember:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            member = await uow.team.get_member(team_id, user_id)
        if not member:
            raise ForbiddenError("无权访问该团队")
        if allow_member:
            return member
        if member.role not in {TeamRole.OWNER, TeamRole.ADMIN}:
            raise ForbiddenError("需要团队管理员权限")
        return member
