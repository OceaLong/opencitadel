#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AuthService: register_with_invitation / login / logout / issue_tokens_for_user.

Only `refresh` had coverage before this file
(tests/app/infrastructure/security/test_auth_security.py:89); this backfills
the other four public methods. Fake uow + repo shapes mirror that file's
`_FakeUow`/`_FakeUserRepo`/`_FakeRefreshRepo` construction pattern.
"""
from datetime import datetime, timedelta

import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError, UnauthorizedError
from app.application.services.auth_service import AuthService
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.refresh_token import RefreshToken
from app.domain.models.user import User, UserStatus
from app.domain.models.team import TeamRole
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.password_hasher import PasswordHasher


class _FakeInvitationRepo:
    def __init__(self, invitations: list[Invitation] | None = None) -> None:
        self.invitations = {item.token: item for item in (invitations or [])}

    async def get_by_token(self, token: str):
        return self.invitations.get(token)

    async def save(self, invitation: Invitation) -> None:
        self.invitations[invitation.token] = invitation


class _FakeUserRepo:
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

    async def save(self, user: User) -> None:
        self.users[user.id] = user


class _FakeRefreshRepo:
    def __init__(self) -> None:
        self.tokens: dict[str, RefreshToken] = {}
        self.revoked_hashes: list[str] = []

    async def save(self, token: RefreshToken) -> None:
        self.tokens[token.token_hash] = token

    async def consume_by_hash(self, token_hash: str):
        token = self.tokens.get(token_hash)
        if not token or token.revoked:
            return None
        token.revoked_at = token.created_at
        return token

    async def revoke_by_hash(self, token_hash: str) -> None:
        self.revoked_hashes.append(token_hash)
        token = self.tokens.get(token_hash)
        if token:
            token.revoked_at = token.created_at


class _FakeUow:
    def __init__(self, invitation_repo=None, user_repo=None, refresh_repo=None) -> None:
        self.invitation = invitation_repo or _FakeInvitationRepo()
        self.user = user_repo or _FakeUserRepo()
        self.refresh_token = refresh_repo or _FakeRefreshRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _build_service(*, invitation_repo=None, user_repo=None, refresh_repo=None) -> AuthService:
    return AuthService(
        uow_factory=lambda: _FakeUow(invitation_repo, user_repo, refresh_repo),
        password_hasher=PasswordHasher(),
        jwt_service=JwtService(secret="test-secret", access_ttl_seconds=60, refresh_ttl_seconds=120),
    )


def _platform_invitation(**overrides) -> Invitation:
    fields = dict(
        type=InvitationType.PLATFORM,
        email="new@example.com",
        token="invite-token",
        expires_at=datetime.now() + timedelta(days=1),
    )
    fields.update(overrides)
    return Invitation(**fields)


# ---------------------------------------------------------------------------
# register_with_invitation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_with_invitation_success_creates_user_and_marks_accepted():
    invitation = _platform_invitation()
    invitation_repo = _FakeInvitationRepo([invitation])
    user_repo = _FakeUserRepo()
    service = _build_service(invitation_repo=invitation_repo, user_repo=user_repo)

    user = await service.register_with_invitation(
        invite_token="invite-token",
        email="New@Example.com",
        username="newuser",
        password="password123",
    )

    assert user.email == "new@example.com"
    assert user.username == "newuser"
    assert user_repo.users[user.id] is user
    saved_invitation = invitation_repo.invitations["invite-token"]
    assert saved_invitation.accepted_at is not None
    assert saved_invitation.accepted_user_id == user.id


@pytest.mark.asyncio
async def test_register_with_invitation_invalid_token_raises_bad_request():
    service = _build_service(invitation_repo=_FakeInvitationRepo([]))

    with pytest.raises(BadRequestError, match="邀请链接无效"):
        await service.register_with_invitation(
            invite_token="missing-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_wrong_type_raises_bad_request():
    """A TEAM invitation token must not be usable for platform registration."""
    invitation = _platform_invitation(type=InvitationType.TEAM, team_id="team-1", team_role=TeamRole.MEMBER)
    service = _build_service(invitation_repo=_FakeInvitationRepo([invitation]))

    with pytest.raises(BadRequestError, match="邀请链接无效"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_already_used_raises_bad_request():
    invitation = _platform_invitation(accepted_at=datetime.now(), accepted_user_id="someone")
    service = _build_service(invitation_repo=_FakeInvitationRepo([invitation]))

    with pytest.raises(BadRequestError, match="邀请链接已被使用"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_expired_raises_bad_request():
    invitation = _platform_invitation(expires_at=datetime.now() - timedelta(days=1))
    service = _build_service(invitation_repo=_FakeInvitationRepo([invitation]))

    with pytest.raises(BadRequestError, match="邀请链接已过期"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_email_mismatch_raises_bad_request():
    invitation = _platform_invitation(email="expected@example.com")
    service = _build_service(invitation_repo=_FakeInvitationRepo([invitation]))

    with pytest.raises(BadRequestError, match="注册邮箱与邀请不匹配"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="other@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_email_already_registered_raises_conflict():
    invitation = _platform_invitation()
    existing_user = User(email="new@example.com", username="existing")
    service = _build_service(
        invitation_repo=_FakeInvitationRepo([invitation]),
        user_repo=_FakeUserRepo([existing_user]),
    )

    with pytest.raises(ConflictError, match="邮箱已注册"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


@pytest.mark.asyncio
async def test_register_with_invitation_username_taken_raises_conflict():
    invitation = _platform_invitation()
    existing_user = User(email="someone-else@example.com", username="newuser")
    service = _build_service(
        invitation_repo=_FakeInvitationRepo([invitation]),
        user_repo=_FakeUserRepo([existing_user]),
    )

    with pytest.raises(ConflictError, match="用户名已存在"):
        await service.register_with_invitation(
            invite_token="invite-token",
            email="new@example.com",
            username="newuser",
            password="password123",
        )


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success_returns_user_and_tokens():
    hasher = PasswordHasher()
    user = User(email="u@example.com", username="u1", password_hash=hasher.hash("correct-pw"), status=UserStatus.ACTIVE)
    service = _build_service(user_repo=_FakeUserRepo([user]))

    logged_in, tokens = await service.login(email_or_username="u@example.com", password="correct-pw")

    assert logged_in.id == user.id
    assert logged_in.last_login_at is not None
    assert tokens.access_token
    assert tokens.refresh_token


@pytest.mark.asyncio
async def test_login_by_username_success():
    hasher = PasswordHasher()
    user = User(email="u@example.com", username="u1", password_hash=hasher.hash("correct-pw"), status=UserStatus.ACTIVE)
    service = _build_service(user_repo=_FakeUserRepo([user]))

    logged_in, _ = await service.login(email_or_username="u1", password="correct-pw")

    assert logged_in.id == user.id


@pytest.mark.asyncio
async def test_login_wrong_password_raises_unauthorized():
    hasher = PasswordHasher()
    user = User(email="u@example.com", username="u1", password_hash=hasher.hash("correct-pw"), status=UserStatus.ACTIVE)
    service = _build_service(user_repo=_FakeUserRepo([user]))

    with pytest.raises(UnauthorizedError, match="账号或密码错误"):
        await service.login(email_or_username="u@example.com", password="wrong-pw")


@pytest.mark.asyncio
async def test_login_unknown_user_raises_unauthorized_with_same_message():
    """Timing-safe semantics: an unknown identifier must raise the exact
    same error message as a wrong password, so callers cannot distinguish
    "no such account" from "wrong password" by error text alone."""
    service = _build_service(user_repo=_FakeUserRepo([]))

    with pytest.raises(UnauthorizedError, match="账号或密码错误"):
        await service.login(email_or_username="ghost@example.com", password="whatever")


@pytest.mark.asyncio
async def test_login_disabled_account_raises_unauthorized():
    hasher = PasswordHasher()
    user = User(
        email="u@example.com",
        username="u1",
        password_hash=hasher.hash("correct-pw"),
        status=UserStatus.DISABLED,
    )
    service = _build_service(user_repo=_FakeUserRepo([user]))

    with pytest.raises(UnauthorizedError, match="账号已被禁用"):
        await service.login(email_or_username="u@example.com", password="correct-pw")


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_revokes_refresh_token():
    jwt_service = JwtService(secret="test-secret", access_ttl_seconds=60, refresh_ttl_seconds=120)
    refresh_repo = _FakeRefreshRepo()
    service = AuthService(
        uow_factory=lambda: _FakeUow(refresh_repo=refresh_repo),
        password_hasher=PasswordHasher(),
        jwt_service=jwt_service,
    )
    refresh_token = jwt_service.issue_refresh_token(user_id="user-1", token_version=0)
    token_hash = jwt_service.hash_token(refresh_token)
    await refresh_repo.save(RefreshToken(user_id="user-1", token_hash=token_hash, expires_at=datetime.now() + timedelta(days=1)))

    await service.logout(refresh_token)

    assert token_hash in refresh_repo.revoked_hashes
    assert refresh_repo.tokens[token_hash].revoked is True


@pytest.mark.asyncio
async def test_logout_with_no_token_is_a_noop():
    refresh_repo = _FakeRefreshRepo()
    service = _build_service(refresh_repo=refresh_repo)

    await service.logout(None)

    assert refresh_repo.revoked_hashes == []


# ---------------------------------------------------------------------------
# issue_tokens_for_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_tokens_for_user_persists_refresh_token_and_returns_pair():
    refresh_repo = _FakeRefreshRepo()
    service = _build_service(refresh_repo=refresh_repo)
    user = User(email="u@example.com", username="u1", token_version=2)

    tokens = await service.issue_tokens_for_user(user, user_agent="pytest-agent", ip_address="127.0.0.1")

    assert tokens.access_token
    assert tokens.refresh_token
    assert len(refresh_repo.tokens) == 1
    stored = next(iter(refresh_repo.tokens.values()))
    assert stored.user_id == user.id
    assert stored.user_agent == "pytest-agent"
    assert stored.ip_address == "127.0.0.1"
