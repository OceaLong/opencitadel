#!/usr/bin/env python
# -*- coding: utf-8 -*-
import secrets
from datetime import datetime, timedelta
from typing import Callable, List

from app.application.errors.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.repositories.uow import IUnitOfWork
from app.interfaces.schemas.team import TeamMemberDetailResponse
from core.config import get_settings


class TeamService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def create_team(self, *, name: str, description: str, actor_user_id: str) -> Team:
        name = name.strip()
        description = description.strip()
        if not name:
            raise BadRequestError("团队名称不能为空")
        team = Team(name=name, description=description, created_by=actor_user_id)
        async with self._uow_factory() as uow:
            await uow.team.save(team)
            await uow.team.add_member(TeamMember(team_id=team.id, user_id=actor_user_id, role=TeamRole.OWNER))
        return team

    async def list_my_teams(self, user_id: str) -> List[Team]:
        async with self._uow_factory() as uow:
            return await uow.team.list_for_user(user_id)

    async def get_team(self, team_id: str, actor_user_id: str) -> Team:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            await self._load_actor_member(uow, team_id, actor_user_id, allow_member=True)
            return team

    async def list_members(self, team_id: str, actor_user_id: str) -> List[TeamMember]:
        async with self._uow_factory() as uow:
            await self._load_actor_member(uow, team_id, actor_user_id, allow_member=True)
            return await uow.team.list_members(team_id)

    async def list_member_details(self, team_id: str, actor_user_id: str) -> List[TeamMemberDetailResponse]:
        async with self._uow_factory() as uow:
            await self._load_actor_member(uow, team_id, actor_user_id, allow_member=True)
            members = await uow.team.list_members(team_id)
            return await self._enrich_members(uow, members)

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

            existing = await uow.team.get_member(invitation.team_id, user_id)
            if existing:
                invitation.accepted_at = datetime.now()
                invitation.accepted_user_id = user_id
                await uow.invitation.save(invitation)
                return existing

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

    async def delete_team(self, *, team_id: str, actor_user_id: str) -> None:
        async with self._uow_factory() as uow:
            actor = await self._load_actor_member(uow, team_id, actor_user_id, allow_member=False)
            if actor.role != TeamRole.OWNER:
                raise ForbiddenError("只有团队所有者可解散团队")
            await uow.team.delete_by_id(team_id)

    async def remove_member(self, *, team_id: str, actor_user_id: str, target_user_id: str) -> None:
        async with self._uow_factory() as uow:
            await self._load_actor_member(uow, team_id, actor_user_id, allow_member=False)
            target = await uow.team.get_member(team_id, target_user_id)
            if not target:
                raise NotFoundError("成员不存在")
            await self._ensure_removable_owner(uow, team_id, target)
            await uow.team.remove_member(team_id, target_user_id)

    async def update_member_role(
            self,
            *,
            team_id: str,
            actor_user_id: str,
            target_user_id: str,
            role: TeamRole,
    ) -> TeamMember:
        async with self._uow_factory() as uow:
            actor = await self._load_actor_member(uow, team_id, actor_user_id, allow_member=False)
            if actor.role != TeamRole.OWNER:
                raise ForbiddenError("只有团队所有者可修改成员角色")
            target = await uow.team.get_member(team_id, target_user_id)
            if not target:
                raise NotFoundError("成员不存在")
            if target.role == TeamRole.OWNER and role != TeamRole.OWNER:
                await self._ensure_removable_owner(uow, team_id, target)
            await uow.team.update_member_role(team_id, target_user_id, role)
            updated = await uow.team.get_member(team_id, target_user_id)
            if not updated:
                raise NotFoundError("成员不存在")
            return updated

    async def leave_team(self, *, team_id: str, user_id: str) -> None:
        async with self._uow_factory() as uow:
            member = await self._load_actor_member(uow, team_id, user_id, allow_member=True)
        if member.role == TeamRole.OWNER:
            members = await uow.team.list_members(team_id)
            owner_count = sum(1 for item in members if item.role == TeamRole.OWNER)
            if owner_count <= 1:
                raise BadRequestError("请先转移所有权或解散团队")
        await uow.team.remove_member(team_id, user_id)

    async def admin_list_all(self, *, limit: int, offset: int) -> tuple[List[Team], int]:
        async with self._uow_factory() as uow:
            teams = await uow.team.list_all(limit=limit, offset=offset)
            total = await uow.team.count()
        return teams, total

    async def admin_delete_team(self, team_id: str) -> None:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            await uow.team.delete_by_id(team_id)

    async def admin_list_members(self, team_id: str) -> List[TeamMember]:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            return await uow.team.list_members(team_id)

    async def admin_list_member_details(self, team_id: str) -> List[TeamMemberDetailResponse]:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            members = await uow.team.list_members(team_id)
            return await self._enrich_members(uow, members)

    async def admin_remove_member(self, team_id: str, target_user_id: str) -> None:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            target = await uow.team.get_member(team_id, target_user_id)
            if not target:
                raise NotFoundError("成员不存在")
            await self._ensure_removable_owner(uow, team_id, target)
            await uow.team.remove_member(team_id, target_user_id)

    async def admin_update_member_role(self, team_id: str, target_user_id: str, role: TeamRole) -> TeamMember:
        async with self._uow_factory() as uow:
            team = await uow.team.get_by_id(team_id)
            if not team:
                raise NotFoundError("团队不存在")
            target = await uow.team.get_member(team_id, target_user_id)
            if not target:
                raise NotFoundError("成员不存在")
            if target.role == TeamRole.OWNER and role != TeamRole.OWNER:
                await self._ensure_removable_owner(uow, team_id, target)
            await uow.team.update_member_role(team_id, target_user_id, role)
            updated = await uow.team.get_member(team_id, target_user_id)
            if not updated:
                raise NotFoundError("成员不存在")
            return updated

    async def _enrich_members(self, uow, members: List[TeamMember]) -> List[TeamMemberDetailResponse]:
        if not members:
            return []
        user_ids = [member.user_id for member in members]
        users = await uow.user.list_by_ids(user_ids)
        user_map = {user.id: user for user in users}
        return [
            TeamMemberDetailResponse(
                user_id=member.user_id,
                role=member.role,
                joined_at=member.joined_at,
                display_name=user_map[member.user_id].display_name if member.user_id in user_map else "",
                email=user_map[member.user_id].email if member.user_id in user_map else "",
                avatar_url=user_map[member.user_id].avatar_url if member.user_id in user_map else "",
            )
            for member in members
        ]

    async def _ensure_removable_owner(self, uow, team_id: str, target: TeamMember) -> None:
        if target.role != TeamRole.OWNER:
            return
        members = await uow.team.list_members(team_id)
        owner_count = sum(1 for member in members if member.role == TeamRole.OWNER)
        if owner_count <= 1:
            raise BadRequestError("不能移除或降级唯一的所有者")

    async def _load_actor_member(
            self,
            uow,
            team_id: str,
            user_id: str,
            *,
            allow_member: bool = False,
    ) -> TeamMember:
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

    async def _require_team_admin(self, team_id: str, user_id: str, *, allow_member: bool = False) -> TeamMember:
        async with self._uow_factory() as uow:
            return await self._load_actor_member(uow, team_id, user_id, allow_member=allow_member)
