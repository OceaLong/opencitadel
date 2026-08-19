#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TeamService: membership/admin-guard/ownership coverage.

The invitation-flow methods (`preview_invitation`, `register_and_accept_invitation`,
`accept_invitation`, `create_team_invitation`, `leave_team`'s uow-boundary test)
already have coverage in test_team_invitation_service.py; this file focuses on
the remaining membership/admin surface: remove_member / update_member_role /
leave_team owner-protection, delete_team permission, the four admin_* guards,
and get_team/list_members non-member access. Fake repo shapes are copied from
test_team_invitation_service.py's InMemory*Repo/FakeUow pattern.
"""
from datetime import datetime, timedelta

import pytest

from app.domain.errors import BadRequestError, ForbiddenError, NotFoundError
from app.application.services.team_service import TeamService
from app.domain.models.invitation import Invitation
from app.domain.models.team import Team, TeamMember, TeamRole
from app.domain.models.user import User


class InMemoryInvitationRepo:
    def __init__(self, invitations: list[Invitation] | None = None) -> None:
        self.invitations = {item.token: item for item in (invitations or [])}

    async def get_by_token(self, token: str):
        return self.invitations.get(token)

    async def save(self, invitation: Invitation) -> None:
        self.invitations[invitation.token] = invitation


class InMemoryTeamRepo:
    def __init__(self, teams: list[Team] | None = None, members: list[TeamMember] | None = None) -> None:
        self.teams = {team.id: team for team in (teams or [])}
        self.members = {(member.team_id, member.user_id): member for member in (members or [])}
        self.deleted_team_ids: list[str] = []

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
        self.deleted_team_ids.append(team_id)
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


def _team_with_members(*members: TeamMember, team_id: str = "team-1") -> tuple[Team, InMemoryTeamRepo]:
    team = Team(id=team_id, name="Product", description="", created_by=members[0].user_id if members else "owner-1")
    team_repo = InMemoryTeamRepo([team], list(members))
    return team, team_repo


def _build_service(team_repo: InMemoryTeamRepo, user_repo: InMemoryUserRepo | None = None) -> TeamService:
    return TeamService(
        uow_factory=lambda: FakeUow(InMemoryInvitationRepo(), team_repo, user_repo or InMemoryUserRepo())
    )


# ---------------------------------------------------------------------------
# get_team / list_members — non-member access
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_team_returns_team_for_member():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    result = await service.get_team("team-1", "owner-1")

    assert result.id == "team-1"


@pytest.mark.anyio
async def test_get_team_non_member_raises_forbidden():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError):
        await service.get_team("team-1", "stranger")


@pytest.mark.anyio
async def test_get_team_missing_team_raises_not_found():
    team_repo = InMemoryTeamRepo([], [])
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.get_team("no-such-team", "someone")


@pytest.mark.anyio
async def test_list_members_non_member_raises_forbidden():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError):
        await service.list_members("team-1", "stranger")


@pytest.mark.anyio
async def test_list_members_member_sees_roster():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    members = await service.list_members("team-1", "member-1")

    assert {m.user_id for m in members} == {"owner-1", "member-1"}


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_remove_member_success():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    await service.remove_member(team_id="team-1", actor_user_id="owner-1", target_user_id="member-1")

    assert ("team-1", "member-1") not in team_repo.members


@pytest.mark.anyio
async def test_remove_member_non_admin_actor_raises_forbidden():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
        TeamMember(team_id="team-1", user_id="member-2", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError):
        await service.remove_member(team_id="team-1", actor_user_id="member-1", target_user_id="member-2")


@pytest.mark.anyio
async def test_remove_member_missing_target_raises_not_found():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.remove_member(team_id="team-1", actor_user_id="owner-1", target_user_id="ghost")


@pytest.mark.anyio
async def test_remove_member_last_owner_is_protected():
    """`_ensure_removable_owner`: removing the sole owner must fail even
    when the actor is themselves an admin acting on the owner."""
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(BadRequestError, match="不能移除或降级唯一的所有者"):
        await service.remove_member(team_id="team-1", actor_user_id="owner-1", target_user_id="owner-1")

    assert ("team-1", "owner-1") in team_repo.members


@pytest.mark.anyio
async def test_remove_member_non_sole_owner_is_removable():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="owner-2", role=TeamRole.OWNER),
    )
    service = _build_service(team_repo)

    await service.remove_member(team_id="team-1", actor_user_id="owner-1", target_user_id="owner-2")

    assert ("team-1", "owner-2") not in team_repo.members


# ---------------------------------------------------------------------------
# update_member_role
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_update_member_role_success_by_owner():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    updated = await service.update_member_role(
        team_id="team-1", actor_user_id="owner-1", target_user_id="member-1", role=TeamRole.ADMIN
    )

    assert updated.role == TeamRole.ADMIN


@pytest.mark.anyio
async def test_update_member_role_by_non_owner_raises_forbidden():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="admin-1", role=TeamRole.ADMIN),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError, match="只有团队所有者可修改成员角色"):
        await service.update_member_role(
            team_id="team-1", actor_user_id="admin-1", target_user_id="member-1", role=TeamRole.ADMIN
        )


@pytest.mark.anyio
async def test_update_member_role_downgrading_last_owner_is_protected():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(BadRequestError, match="不能移除或降级唯一的所有者"):
        await service.update_member_role(
            team_id="team-1", actor_user_id="owner-1", target_user_id="owner-1", role=TeamRole.ADMIN
        )


@pytest.mark.anyio
async def test_update_member_role_missing_target_raises_not_found():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.update_member_role(
            team_id="team-1", actor_user_id="owner-1", target_user_id="ghost", role=TeamRole.ADMIN
        )


# ---------------------------------------------------------------------------
# leave_team — owner protection (positive path already covered by
# test_team_invitation_service.py's uow-boundary test)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_leave_team_last_owner_is_protected():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(BadRequestError, match="请先转移所有权或解散团队"):
        await service.leave_team(team_id="team-1", user_id="owner-1")

    assert ("team-1", "owner-1") in team_repo.members


@pytest.mark.anyio
async def test_leave_team_non_owner_member_leaves_freely():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    await service.leave_team(team_id="team-1", user_id="member-1")

    assert ("team-1", "member-1") not in team_repo.members


# ---------------------------------------------------------------------------
# delete_team — permission
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_delete_team_by_owner_succeeds():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    await service.delete_team(team_id="team-1", actor_user_id="owner-1")

    assert "team-1" in team_repo.deleted_team_ids


@pytest.mark.anyio
async def test_delete_team_by_admin_raises_forbidden():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="admin-1", role=TeamRole.ADMIN),
    )
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError, match="只有团队所有者可解散团队"):
        await service.delete_team(team_id="team-1", actor_user_id="admin-1")

    assert "team-1" not in team_repo.deleted_team_ids


@pytest.mark.anyio
async def test_delete_team_non_member_raises_forbidden():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(ForbiddenError):
        await service.delete_team(team_id="team-1", actor_user_id="stranger")


# ---------------------------------------------------------------------------
# admin_* — platform-admin guards (team-scope membership does not apply;
# these operate directly on team_id without an actor-membership check)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_admin_delete_team_removes_existing_team():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    await service.admin_delete_team("team-1")

    assert "team-1" in team_repo.deleted_team_ids


@pytest.mark.anyio
async def test_admin_delete_team_missing_team_raises_not_found():
    team_repo = InMemoryTeamRepo([], [])
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.admin_delete_team("no-such-team")


@pytest.mark.anyio
async def test_admin_list_member_details_missing_team_raises_not_found():
    team_repo = InMemoryTeamRepo([], [])
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.admin_list_member_details("no-such-team")


@pytest.mark.anyio
async def test_admin_list_member_details_enriches_with_user_profile():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    user = User(id="owner-1", email="owner@example.com", username="owner1", display_name="Owner One")
    service = _build_service(team_repo, InMemoryUserRepo([user]))

    details = await service.admin_list_member_details("team-1")

    assert len(details) == 1
    assert details[0].email == "owner@example.com"
    assert details[0].display_name == "Owner One"


@pytest.mark.anyio
async def test_admin_remove_member_missing_team_raises_not_found():
    team_repo = InMemoryTeamRepo([], [])
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.admin_remove_member("no-such-team", "member-1")


@pytest.mark.anyio
async def test_admin_remove_member_protects_last_owner():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(BadRequestError, match="不能移除或降级唯一的所有者"):
        await service.admin_remove_member("team-1", "owner-1")


@pytest.mark.anyio
async def test_admin_remove_member_succeeds_for_non_owner():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    await service.admin_remove_member("team-1", "member-1")

    assert ("team-1", "member-1") not in team_repo.members


@pytest.mark.anyio
async def test_admin_update_member_role_missing_team_raises_not_found():
    team_repo = InMemoryTeamRepo([], [])
    service = _build_service(team_repo)

    with pytest.raises(NotFoundError):
        await service.admin_update_member_role("no-such-team", "member-1", TeamRole.ADMIN)


@pytest.mark.anyio
async def test_admin_update_member_role_protects_last_owner_downgrade():
    team, team_repo = _team_with_members(TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER))
    service = _build_service(team_repo)

    with pytest.raises(BadRequestError, match="不能移除或降级唯一的所有者"):
        await service.admin_update_member_role("team-1", "owner-1", TeamRole.ADMIN)


@pytest.mark.anyio
async def test_admin_update_member_role_succeeds():
    team, team_repo = _team_with_members(
        TeamMember(team_id="team-1", user_id="owner-1", role=TeamRole.OWNER),
        TeamMember(team_id="team-1", user_id="member-1", role=TeamRole.MEMBER),
    )
    service = _build_service(team_repo)

    updated = await service.admin_update_member_role("team-1", "member-1", TeamRole.ADMIN)

    assert updated.role == TeamRole.ADMIN
