#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError
from app.application.services.team_service import TeamService
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.models.user import User
from app.interfaces.schemas.admin import InvitationStatus


class InMemoryInvitationRepo:
    def __init__(self, invitations: list[Invitation] | None = None) -> None:
        self.invitations = {item.token: item for item in (invitations or [])}

    async def get_by_token(self, token: str):
        return self.invitations.get(token)

    async def get_pending_team_invitation(self, team_id: str, email: str):
        normalized = email.strip().lower()
        now = datetime.now()
        for invitation in self.invitations.values():
            if (
                    invitation.type == InvitationType.TEAM
                    and invitation.team_id == team_id
                    and invitation.email == normalized
                    and invitation.accepted_at is None
                    and invitation.expires_at > now
            ):
                return invitation
        return None

    async def list(self, invitation_type=None, limit: int = 100, offset: int = 0):
        items = list(self.invitations.values())
        if invitation_type is not None:
            items = [item for item in items if item.type == invitation_type]
        return items[offset: offset + limit]

    async def count(self, invitation_type=None) -> int:
        return len(await self.list(invitation_type=invitation_type))

    async def save(self, invitation: Invitation) -> None:
        self.invitations[invitation.token] = invitation

    async def delete_by_id(self, invitation_id: str) -> None:
        self.invitations = {
            token: invitation
            for token, invitation in self.invitations.items()
            if invitation.id != invitation_id
        }


class InMemoryTeamRepo:
    def __init__(self, teams: list[Team] | None = None, members: list[TeamMember] | None = None) -> None:
        self.teams = {team.id: team for team in (teams or [])}
        self.members = {(member.team_id, member.user_id): member for member in (members or [])}

    async def get_by_id(self, team_id: str):
        return self.teams.get(team_id)

    async def add_member(self, member: TeamMember) -> None:
        self.members[(member.team_id, member.user_id)] = member

    async def get_member(self, team_id: str, user_id: str):
        return self.members.get((team_id, user_id))

    async def list_for_user(self, user_id: str):
        team_ids = {member.team_id for member in self.members.values() if member.user_id == user_id}
        return [self.teams[team_id] for team_id in team_ids if team_id in self.teams]

    async def list_members(self, team_id: str):
        return [member for member in self.members.values() if member.team_id == team_id]

    async def list_all(self, limit: int = 100, offset: int = 0):
        return list(self.teams.values())[offset: offset + limit]

    async def count(self) -> int:
        return len(self.teams)

    async def save(self, team: Team) -> None:
        self.teams[team.id] = team

    async def delete_by_id(self, team_id: str) -> None:
        self.teams.pop(team_id, None)

    async def remove_member(self, team_id: str, user_id: str) -> None:
        self.members.pop((team_id, user_id), None)

    async def update_member_role(self, team_id: str, user_id: str, role: TeamRole) -> None:
        member = self.members.get((team_id, user_id))
        if member:
            self.members[(team_id, user_id)] = member.model_copy(update={"role": role})


class InMemoryUserRepo:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = {user.id: user for user in (users or [])}

    async def get_by_id(self, user_id: str):
        return self.users.get(user_id)

    async def get_by_email(self, email: str):
        normalized = email.lower()
        for user in self.users.values():
            if user.email.lower() == normalized:
                return user
        return None

    async def get_by_username(self, username: str):
        for user in self.users.values():
            if user.username == username:
                return user
        return None

    async def list_by_ids(self, user_ids: list[str]):
        return [self.users[user_id] for user_id in user_ids if user_id in self.users]

    async def save(self, user: User) -> None:
        self.users[user.id] = user


class FakeUow:
    def __init__(self, invitation_repo, team_repo, user_repo) -> None:
        self.invitation = invitation_repo
        self.team = team_repo
        self.user = user_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build_service(
        *,
        invitation: Invitation,
        team: Team,
        users: list[User] | None = None,
) -> TeamService:
    invitation_repo = InMemoryInvitationRepo([invitation])
    team_repo = InMemoryTeamRepo([team])
    user_repo = InMemoryUserRepo(users or [])
    return TeamService(uow_factory=lambda: FakeUow(invitation_repo, team_repo, user_repo))


@pytest.mark.anyio
async def test_preview_invitation_requires_registration_for_new_email():
    team = Team(id="team-1", name="Product", description="", created_by="owner-1")
    invitation = Invitation(
        type=InvitationType.TEAM,
        email="new@example.com",
        team_id=team.id,
        team_role=TeamRole.MEMBER,
        token="token-1",
        expires_at=datetime.now() + timedelta(days=1),
    )
    service = _build_service(invitation=invitation, team=team)

    preview = await service.preview_invitation(token="token-1")

    assert preview.team_name == "Product"
    assert preview.status == InvitationStatus.PENDING
    assert preview.requires_registration is True
    assert preview.email_hint == "n***@example.com"


@pytest.mark.anyio
async def test_register_and_accept_invitation_creates_user_and_member():
    team = Team(id="team-1", name="Product", description="", created_by="owner-1")
    invitation = Invitation(
        type=InvitationType.TEAM,
        email="new@example.com",
        team_id=team.id,
        team_role=TeamRole.ADMIN,
        token="token-1",
        expires_at=datetime.now() + timedelta(days=1),
    )
    service = _build_service(invitation=invitation, team=team)

    result = await service.register_and_accept_invitation(
        token="token-1",
        email="new@example.com",
        username="newuser",
        password="password123",
    )

    assert result.user.email == "new@example.com"
    assert result.member.team_id == "team-1"
    assert result.member.role == TeamRole.ADMIN


@pytest.mark.anyio
async def test_register_and_accept_rejects_open_invite_without_email():
    team = Team(id="team-1", name="Product", description="", created_by="owner-1")
    invitation = Invitation(
        type=InvitationType.TEAM,
        team_id=team.id,
        team_role=TeamRole.MEMBER,
        token="token-1",
        expires_at=datetime.now() + timedelta(days=1),
    )
    service = _build_service(invitation=invitation, team=team)

    with pytest.raises(BadRequestError, match="不支持注册"):
        await service.register_and_accept_invitation(
            token="token-1",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.anyio
async def test_accept_invitation_enforces_email_match():
    team = Team(id="team-1", name="Product", description="", created_by="owner-1")
    invitation = Invitation(
        type=InvitationType.TEAM,
        email="member@example.com",
        team_id=team.id,
        team_role=TeamRole.MEMBER,
        token="token-1",
        expires_at=datetime.now() + timedelta(days=1),
    )
    user = User(id="user-1", email="other@example.com", username="other")
    service = _build_service(invitation=invitation, team=team, users=[user])

    with pytest.raises(BadRequestError, match="邀请邮箱与当前账号不匹配"):
        await service.accept_invitation(token="token-1", user_id="user-1")


@pytest.mark.anyio
async def test_create_team_invitation_rejects_duplicate_pending_email(monkeypatch):
    team = Team(id="team-1", name="Product", description="", created_by="owner-1")
    invitation = Invitation(
        type=InvitationType.TEAM,
        email="member@example.com",
        team_id=team.id,
        team_role=TeamRole.MEMBER,
        token="token-1",
        expires_at=datetime.now() + timedelta(days=1),
    )
    invitation_repo = InMemoryInvitationRepo([invitation])
    team_repo = InMemoryTeamRepo([team], [TeamMember(team_id=team.id, user_id="owner-1", role=TeamRole.OWNER)])
    user_repo = InMemoryUserRepo()
    service = TeamService(uow_factory=lambda: FakeUow(invitation_repo, team_repo, user_repo))
    monkeypatch.setattr(
        "app.application.services.team_service.get_settings",
        lambda: SimpleNamespace(frontend_base_url="http://localhost:8088"),
    )

    with pytest.raises(ConflictError, match="已有待处理的团队邀请"):
        await service.create_team_invitation(
            team_id=team.id,
            actor_user_id="owner-1",
            role=TeamRole.MEMBER,
            email="member@example.com",
        )
