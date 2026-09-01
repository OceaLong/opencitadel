"""OAuth account-takeover hardening (F12).

Covers three invariants of the OAuth login/registration flow in
``auth_routes.py``:

* ``_resolve_oauth_registration_invitation`` must reject expired invitations.
* ``_load_oauth_profile`` must never trust a self-asserted GitHub email; it may
  report an address verified only when GitHub returns it as primary AND
  verified (resolved from ``/user/emails``).
* ``oauth_callback`` must refuse any flow whose email is not provider-verified,
  which is what makes the implicit ``get_by_email`` account link safe, and must
  link a verified email onto an existing account.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.interfaces.endpoints import auth_routes
from app.interfaces.endpoints.auth_routes import (
    _load_oauth_profile,
    _resolve_oauth_registration_invitation,
)
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import (
    get_application_urls,
    get_auth_service,
    get_cookie_manager,
    get_oauth_registry,
    get_uow_factory,
)


def _uow_with_platform_invitations(invitations):
    # Mirrors DBInvitationRepository.get_pending_platform_invitation: the repo
    # query itself filters to a non-accepted, non-expired invitation matching
    # the email, so the fake applies the same predicate.
    async def _get_pending_platform_invitation(email):
        now = datetime.now(UTC)
        normalized = email.strip().lower()
        for invitation in invitations:
            if (
                invitation.email
                and invitation.email.strip().lower() == normalized
                and not invitation.accepted
                and invitation.expires_at > now
            ):
                return invitation
        return None

    return SimpleNamespace(
        invitation=SimpleNamespace(
            get_by_token=AsyncMock(return_value=None),
            get_pending_platform_invitation=_get_pending_platform_invitation,
        )
    )


@pytest.mark.asyncio
async def test_oauth_platform_invitation_rejects_expired():
    # Regression: the OAuth PLATFORM branch only checked `not accepted`, so an
    # expired invitation could still be redeemed via SSO (the password path and
    # the TEAM branch both check expires_at).
    expired = SimpleNamespace(
        email="user@example.com",
        accepted=False,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    uow = _uow_with_platform_invitations([expired])

    result = await _resolve_oauth_registration_invitation(
        uow, email="user@example.com", team_invite_token=""
    )

    assert result is None


@pytest.mark.asyncio
async def test_oauth_platform_invitation_accepts_valid():
    valid = SimpleNamespace(
        email="user@example.com",
        accepted=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    uow = _uow_with_platform_invitations([valid])

    result = await _resolve_oauth_registration_invitation(
        uow, email="user@example.com", team_invite_token=""
    )

    assert result is valid


# --- GitHub email verification (_load_oauth_profile) ------------------------


def _github_client(*, user_payload, emails_payload):
    """Fake authlib-style client: ``get(path, token=...)`` -> response.json()."""

    async def _get(path, token=None):
        payload = emails_payload if path == "user/emails" else user_payload
        return SimpleNamespace(json=lambda payload=payload: payload)

    return SimpleNamespace(get=_get)


@pytest.mark.asyncio
async def test_github_public_email_is_never_trusted_as_verified():
    # Account-takeover vector: the /user profile carries a self-asserted email
    # that GitHub does NOT vouch for. Even though the primary /user/emails entry
    # is unverified, the profile must not be reported as verified.
    client = _github_client(
        user_payload={"id": 42, "email": "victim@example.com", "login": "attacker"},
        emails_payload=[{"email": "victim@example.com", "primary": True, "verified": False}],
    )

    profile = await _load_oauth_profile("github", client, token={})

    assert profile["email_verified"] is False
    assert not profile["email"]


@pytest.mark.asyncio
async def test_github_verified_primary_email_accepted():
    client = _github_client(
        user_payload={"id": 7, "email": None, "login": "alice", "name": "Alice"},
        emails_payload=[
            {"email": "secondary@example.com", "primary": False, "verified": True},
            {"email": "alice@example.com", "primary": True, "verified": True},
        ],
    )

    profile = await _load_oauth_profile("github", client, token={})

    assert profile["email"] == "alice@example.com"
    assert profile["email_verified"] is True


@pytest.mark.asyncio
async def test_github_verified_but_non_primary_email_not_used():
    # A verified but non-primary address must not be adopted; GitHub only
    # guarantees the primary+verified entry as the account's canonical email.
    client = _github_client(
        user_payload={"id": 9, "email": None, "login": "bob"},
        emails_payload=[{"email": "bob@example.com", "primary": False, "verified": True}],
    )

    profile = await _load_oauth_profile("github", client, token={})

    assert profile["email_verified"] is False
    assert not profile["email"]


# --- oauth_callback association guard ---------------------------------------


class _FakeOAuthClient:
    def __init__(self, *, user_payload, emails_payload):
        self._user_payload = user_payload
        self._emails_payload = emails_payload

    async def authorize_access_token(self, request):
        return {"access_token": "gho_test"}

    async def get(self, path, token=None):
        payload = self._emails_payload if path == "user/emails" else self._user_payload
        return SimpleNamespace(json=lambda: payload)


class _FakeOAuthIdentityRepo:
    def __init__(self):
        self.saved = []

    async def get_by_provider_identity(self, provider, provider_user_id):
        return None

    async def save(self, identity):
        self.saved.append(identity)


class _FakeUserRepo:
    def __init__(self, existing_user):
        self._existing = existing_user
        self.get_by_email_calls = []

    async def get_by_id(self, user_id):
        return self._existing

    async def get_by_email(self, email):
        self.get_by_email_calls.append(email)
        return self._existing

    async def get_by_username(self, username):
        return None

    async def save(self, user):  # pragma: no cover - registration path
        pass


class _FakeUow:
    def __init__(self, *, existing_user):
        self.oauth_identity = _FakeOAuthIdentityRepo()
        self.user = _FakeUserRepo(existing_user)
        self.invitation = SimpleNamespace(
            get_by_token=AsyncMock(return_value=None),
            list=AsyncMock(return_value=[]),
        )
        self.team = SimpleNamespace(get_member=AsyncMock(return_value=None))
        self.committed = False

    async def commit(self):
        self.committed = True


def _build_app(*, uow, github_client):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    register_exception_handlers(app)
    app.include_router(auth_routes.router)

    @asynccontextmanager
    async def _uow_factory(*args, **kwargs):
        yield uow

    app.dependency_overrides[get_uow_factory] = lambda: _uow_factory
    app.dependency_overrides[get_oauth_registry] = lambda: SimpleNamespace(
        get=lambda provider: github_client
    )
    app.dependency_overrides[get_auth_service] = lambda: SimpleNamespace(
        issue_tokens_for_user=AsyncMock(
            return_value=SimpleNamespace(access_token="a", refresh_token="r")
        )
    )
    app.dependency_overrides[get_cookie_manager] = lambda: SimpleNamespace(
        set_auth_cookies=lambda *a, **k: None
    )
    app.dependency_overrides[get_application_urls] = lambda: SimpleNamespace(
        frontend_base_url="http://frontend.test/"
    )
    return app


def test_callback_rejects_unverified_github_email_without_associating():
    # The primary GitHub email is unverified -> the flow must be refused before
    # any get_by_email lookup or identity binding can happen.
    existing = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
    uow = _FakeUow(existing_user=existing)
    client = _FakeOAuthClient(
        user_payload={"id": 42, "email": "victim@example.com", "login": "attacker"},
        emails_payload=[{"email": "victim@example.com", "primary": True, "verified": False}],
    )
    app = _build_app(uow=uow, github_client=client)

    with TestClient(app) as test_client:
        resp = test_client.get("/auth/oauth/github/callback", follow_redirects=False)

    assert resp.status_code == 400
    assert uow.user.get_by_email_calls == []
    assert uow.oauth_identity.saved == []


def test_callback_links_verified_github_email_to_existing_account():
    existing = SimpleNamespace(id="22222222-2222-2222-2222-222222222222")
    uow = _FakeUow(existing_user=existing)
    client = _FakeOAuthClient(
        user_payload={"id": 99, "email": None, "login": "alice", "name": "Alice"},
        emails_payload=[{"email": "alice@example.com", "primary": True, "verified": True}],
    )
    app = _build_app(uow=uow, github_client=client)

    with TestClient(app) as test_client:
        resp = test_client.get("/auth/oauth/github/callback", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert uow.user.get_by_email_calls == ["alice@example.com"]
    assert len(uow.oauth_identity.saved) == 1
    saved = uow.oauth_identity.saved[0]
    assert saved.user_id == existing.id
    assert saved.email == "alice@example.com"
    assert saved.email_verified is True
    assert uow.committed is True
