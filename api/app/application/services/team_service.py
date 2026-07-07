#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from app.application.errors.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.models.user import User
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.security.password_hasher import PasswordHasher
from app.interfaces.schemas.admin import InvitationStatus
from app.interfaces.schemas.team import TeamInvitationPreviewResponse, TeamMemberDetailResponse
from core.config import get_settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class TeamInvitationRegisterResult:
    user: User
    member: TeamMember


class TeamService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            password_hasher: Optional[PasswordHasher] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher or PasswordHasher()

    async def create_team(self, *, name: str, description: str, actor_user_id: str) -> Team:
        name = name.strip()
        description = description.strip()
        if not name:
            raise BadRequestError("团队名称不能为空", error_key="errors.teamNameRequired")
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

    async def create_team_invitation(
            self,
            *,
            team_id: str,
            actor_user_id: str,
            role: TeamRole,
            email: str | None = None,
    ) -> str:
        await self._require_team_admin(team_id, actor_user_id)
        normalized_email = self._normalize_invite_email(email)
        token = secrets.token_urlsafe(32)
        invitation = Invitation(
            type=InvitationType.TEAM,
            email=normalized_email,
            team_id=team_id,
            team_role=role,
            token=token,
            invited_by=actor_user_id,
            expires_at=datetime.now() + timedelta(days=7),
        )
        async with self._uow_factory() as uow:
            if normalized_email:
                existing = await uow.invitation.get_pending_team_invitation(team_id, normalized_email)
                if existing:
                    raise ConflictError("该邮箱已有待处理的团队邀请")
            await uow.invitation.save(invitation)
        return f"{get_settings().frontend_base_url.rstrip('/')}/invitations/{token}"

    async def preview_invitation(self, *, token: str) -> TeamInvitationPreviewResponse:
        async with self._uow_factory() as uow:
            invitation = await self._load_team_invitation(uow, token)
            team = await uow.team.get_by_id(invitation.team_id or "")
            if not team:
                raise NotFoundError("团队不存在")
            now = datetime.now()
            status = self._invitation_status(invitation, now=now)
            requires_registration = False
            email_hint = None
            if invitation.email and status == InvitationStatus.PENDING:
                email_hint = self._mask_email(invitation.email)
                existing_user = await uow.user.get_by_email(invitation.email)
                requires_registration = existing_user is None
            return TeamInvitationPreviewResponse(
                team_id=team.id,
                team_name=team.name,
                role=invitation.team_role or TeamRole.MEMBER,
                status=status,
                expires_at=invitation.expires_at,
                requires_registration=requires_registration,
                email_hint=email_hint,
            )

    async def register_and_accept_invitation(
            self,
            *,
            token: str,
            email: str,
            username: str,
            password: str,
    ) -> TeamInvitationRegisterResult:
        normalized_email = email.strip().lower()
        if not _EMAIL_RE.match(normalized_email):
            raise BadRequestError("邮箱格式无效")
        async with self._uow_factory() as uow:
            invitation = await self._load_team_invitation(uow, token)
            if not invitation.email:
                raise BadRequestError("此邀请不支持注册，请登录已有账号")
            if invitation.email.strip().lower() != normalized_email:
                raise BadRequestError("注册邮箱与邀请不匹配")
            if await uow.user.get_by_email(normalized_email):
                raise ConflictError("邮箱已注册，请直接登录")
            if await uow.user.get_by_username(username):
                raise ConflictError("用户名已存在")

            user = User(
                email=normalized_email,
                username=username,
                password_hash=self._password_hasher.hash(password),
            )
            await uow.user.save(user)
            member = TeamMember(
                team_id=invitation.team_id or "",
                user_id=user.id,
                role=invitation.team_role or TeamRole.MEMBER,
            )
            await uow.team.add_member(member)
            invitation.accepted_at = datetime.now()
            invitation.accepted_user_id = user.id
            await uow.invitation.save(invitation)
            return TeamInvitationRegisterResult(user=user, member=member)

    async def accept_invitation(self, *, token: str, user_id: str) -> TeamMember:
        async with self._uow_factory() as uow:
            invitation = await self._load_team_invitation(uow, token)
            if invitation.accepted:
                raise BadRequestError("邀请链接已被使用")
            if invitation.expires_at < datetime.now():
                raise BadRequestError("邀请链接已过期")

            user = await uow.user.get_by_id(user_id)
            if not user:
                raise BadRequestError("用户不存在")
            if invitation.email and invitation.email.strip().lower() != user.email.strip().lower():
                raise BadRequestError("邀请邮箱与当前账号不匹配")

            existing = await uow.team.get_member(invitation.team_id or "", user_id)
            if existing:
                invitation.accepted_at = datetime.now()
                invitation.accepted_user_id = user_id
                await uow.invitation.save(invitation)
                return existing

            member = TeamMember(
                team_id=invitation.team_id or "",
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
                raise ForbiddenError("只有团队所有者可解散团队", error_key="errors.teamOwnerOnly")
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
                raise ForbiddenError("只有团队所有者可修改成员角色", error_key="errors.teamOwnerOnly")
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
            raise ForbiddenError("无权访问该团队", error_key="errors.teamAccessDenied")
        if allow_member:
            return member
        if member.role not in {TeamRole.OWNER, TeamRole.ADMIN}:
            raise ForbiddenError("需要团队管理员权限")
        return member

    async def _require_team_admin(self, team_id: str, user_id: str, *, allow_member: bool = False) -> TeamMember:
        async with self._uow_factory() as uow:
            return await self._load_actor_member(uow, team_id, user_id, allow_member=allow_member)

    async def _load_team_invitation(self, uow, token: str) -> Invitation:
        invitation = await uow.invitation.get_by_token(token)
        if not invitation or invitation.type != InvitationType.TEAM or not invitation.team_id:
            raise BadRequestError("邀请链接无效")
        return invitation

    @staticmethod
    def _invitation_status(invitation: Invitation, *, now: datetime) -> InvitationStatus:
        if invitation.accepted_at is not None:
            return InvitationStatus.ACCEPTED
        if invitation.expires_at < now:
            return InvitationStatus.EXPIRED
        return InvitationStatus.PENDING

    @staticmethod
    def _normalize_invite_email(email: str | None) -> str | None:
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        if not _EMAIL_RE.match(normalized):
            raise BadRequestError("邮箱格式无效")
        return normalized

    @staticmethod
    def _mask_email(email: str) -> str:
        local, _, domain = email.partition("@")
        if not domain:
            return "***"
        masked_local = f"{local[0]}***" if local else "***"
        return f"{masked_local}@{domain}"
