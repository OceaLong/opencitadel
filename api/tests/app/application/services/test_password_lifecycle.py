"""Password replacement revokes credentials and cannot bypass authorization."""

from types import SimpleNamespace

import pytest

from app.domain.errors import BadRequestError, ConflictError, ForbiddenError, UnauthorizedError
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole, User
from app.infrastructure.security.password_hasher import PasswordHasher
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from tests.app.application.services.test_auth_service import (
    _build_service,
    _FakeAuditRepo,
    _FakeRefreshRepo,
    _FakeUow,
    _FakeUserRepo,
)


class Users(_FakeUserRepo):
    async def replace_password(self, user_id, *, expected_hash, expected_version, password_hash):
        user = self.users[user_id]
        if user.password_hash != expected_hash or user.token_version != expected_version:
            return False
        self.users[user_id] = user.model_copy(
            update={
                "password_hash": password_hash,
                "token_version": user.token_version + 1,
            }
        )
        return True

    async def update_last_login(self, user_id, last_login_at):
        self.users[user_id].last_login_at = last_login_at


class Refresh(_FakeRefreshRepo):
    async def revoke_all_for_user(self, user_id):
        for token in self.tokens.values():
            if token.user_id == user_id:
                token.revoked_at = token.created_at


def setup_account():
    user = User(
        email="a@example.com", username="alice", password_hash=PasswordHasher().hash("old-password")
    )
    users, refresh, audit = Users([user]), Refresh(), _FakeAuditRepo()
    service = _build_service(user_repo=users, refresh_repo=refresh, audit_repo=audit)
    return user, users, refresh, audit, service


@pytest.mark.asyncio
async def test_password_change_revokes_refresh_and_access_and_old_password():
    user, users, _refresh, audit, service = setup_account()
    old_tokens = await service.issue_tokens_for_user(user)
    await service.change_password(
        principal=Principal(user_id=user.id),
        current_password="old-password",
        new_password="new-password",
    )
    assert PasswordHasher().verify("new-password", users.users[user.id].password_hash)
    with pytest.raises(UnauthorizedError):
        await service.refresh(old_tokens.refresh_token)
    with pytest.raises(UnauthorizedError):
        await service.login(email_or_username="alice", password="old-password")
    runtime = SimpleNamespace(
        token_codec=service._token_codec, uow_factory=lambda: _FakeUow(user_repo=users)
    )
    assert (
        await AuthContextMiddleware(None)._principal_from_token(
            old_tokens.access_token, runtime=runtime
        )
        is None
    )
    assert audit.logs[-1].action == "auth.password.change"
    assert "password" not in str(audit.logs[-1].metadata)
    _, tokens = await service.login(email_or_username="alice", password="new-password")
    assert service._token_codec.decode(tokens.access_token, expected_type="access")["ver"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["incorrect", ""])
async def test_current_password_required_and_failed_change_preserves_sessions(password):
    user, users, _refresh, audit, service = setup_account()
    with pytest.raises(UnauthorizedError):
        await service.change_password(
            principal=Principal(user_id=user.id),
            current_password=password,
            new_password="new-password",
        )
    assert users.users[user.id].token_version == 0
    assert not audit.logs


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["short", " " * 8, "a" * 129, "old-password"])
async def test_password_policy_and_reuse_rejected(password):
    user, users, _, _, service = setup_account()
    with pytest.raises(BadRequestError):
        await service.change_password(
            principal=Principal(user_id=user.id),
            current_password="old-password",
            new_password=password,
        )
    assert users.users[user.id].token_version == 0


@pytest.mark.asyncio
async def test_admin_reset_requires_admin_and_revokes_target_sessions():
    user, users, _refresh, audit, service = setup_account()
    actor = User(email="admin@example.com", username="admin", global_role=GlobalRole.ADMIN)
    users.users[actor.id] = actor
    old = await service.issue_tokens_for_user(user)
    with pytest.raises(ForbiddenError):
        await service.reset_password(
            principal=Principal(user_id=user.id), user_id=actor.id, new_password="new-password"
        )
    await service.reset_password(
        principal=Principal(user_id=actor.id, global_role=GlobalRole.ADMIN),
        user_id=user.id,
        new_password="new-password",
    )
    with pytest.raises(UnauthorizedError):
        await service.refresh(old.refresh_token)
    assert audit.logs[-1].actor_user_id == actor.id
    assert audit.logs[-1].resource_id == user.id
    assert audit.logs[-1].action == "admin.user.password.reset"


@pytest.mark.asyncio
async def test_stale_principal_cannot_change_password():
    user, users, _, _, service = setup_account()
    users.users[user.id].token_version = 2
    with pytest.raises(UnauthorizedError):
        await service.change_password(
            principal=Principal(user_id=user.id),
            current_password="old-password",
            new_password="new-password",
        )


@pytest.mark.asyncio
async def test_concurrent_reset_conflict_does_not_report_success():
    user, users, _, audit, service = setup_account()

    async def conflicting_update(*args, **kwargs):
        return False

    users.replace_password = conflicting_update
    with pytest.raises(ConflictError):
        await service.change_password(
            principal=Principal(user_id=user.id),
            current_password="old-password",
            new_password="new-password",
        )
    assert not audit.logs


@pytest.mark.asyncio
async def test_sso_account_cannot_set_password_without_administrator():
    user, users, _, audit, service = setup_account()
    users.users[user.id].password_hash = None
    with pytest.raises(UnauthorizedError):
        await service.change_password(
            principal=Principal(user_id=user.id),
            current_password="anything",
            new_password="new-password",
        )
    assert users.users[user.id].password_hash is None
    assert not audit.logs


@pytest.mark.asyncio
async def test_stale_admin_role_is_checked_against_current_account():
    user, users, _, audit, service = setup_account()
    with pytest.raises(ForbiddenError):
        await service.reset_password(
            principal=Principal(user_id=user.id, global_role=GlobalRole.ADMIN),
            user_id=user.id,
            new_password="new-password",
        )
    assert users.users[user.id].token_version == 0
    assert not audit.logs
