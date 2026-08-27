from datetime import UTC, datetime

import jwt
import pytest

from app.application.services.auth_service import AuthService
from app.domain.errors import UnauthorizedError
from app.domain.models.refresh_token import RefreshToken
from app.domain.models.user import User, UserStatus
from app.infrastructure.adapters.security_ports import JwtTokenCodecAdapter
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher


def test_password_hasher_hashes_and_verifies_password():
    hasher = PasswordHasher()
    password_hash = hasher.hash("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert hasher.verify("correct horse battery staple", password_hash)
    assert not hasher.verify("wrong", password_hash)


def test_password_hasher_verifies_existing_argon2id_hash():
    hasher = PasswordHasher()
    existing_hash = (
        "$argon2id$v=19$m=65536,t=3,p=4$kawm5JfXoOCU27tt0qoIMA$"
        "BFnFIP1yC3CHjVEOp5z6phW344/2eZDNtSNv4bRwg4w"
    )

    assert hasher.verify("correct horse battery staple", existing_hash)
    assert not hasher.verify("wrong", existing_hash)
    assert not hasher.verify("correct horse battery staple", "not-a-hash")


def test_jwt_service_issues_typed_tokens_with_version():
    service = JwtService(
        secret="test-jwt-secret-at-least-32-characters",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )

    access = service.issue_access_token(user_id="u1", role="admin", token_version=3)
    refresh = service.issue_refresh_token(user_id="u1", token_version=3)

    access_claims = service.decode(access, expected_type="access")
    refresh_claims = service.decode(refresh, expected_type="refresh")
    assert access_claims["sub"] == "u1"
    assert access_claims["role"] == "admin"
    assert access_claims["ver"] == 3
    assert refresh_claims["sub"] == "u1"
    assert refresh_claims["ver"] == 3

    with pytest.raises(jwt.InvalidTokenError):
        service.decode(access, expected_type="refresh")


def test_service_api_key_hash_is_stable_and_plaintext_is_one_time_value():
    hasher = ServiceApiKeyHasher()
    generated = hasher.generate()

    assert generated.plaintext.startswith("sk-")
    assert generated.prefix == generated.plaintext[:12]
    assert generated.key_hash == hasher.hash(generated.plaintext)
    assert generated.key_hash != generated.plaintext


class _FakeRefreshRepo:
    def __init__(self, token: RefreshToken):
        self._tokens = {token.token_hash: token}

    async def consume_by_hash(self, token_hash: str):
        token = self._tokens.get(token_hash)
        if not token or token.revoked:
            return None
        token.revoked_at = token.created_at
        return token

    async def save(self, token: RefreshToken):
        self._tokens[token.token_hash] = token


class _FakeUserRepo:
    def __init__(self, user: User):
        self._user = user

    async def get_by_id(self, user_id: str):
        return self._user if self._user.id == user_id else None


class _FakeUow:
    def __init__(self, refresh_repo: _FakeRefreshRepo, user_repo: _FakeUserRepo):
        self.refresh_token = refresh_repo
        self.user = user_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_auth_service_refresh_consumes_token_once():
    jwt_service = JwtService(
        secret="test-jwt-secret-at-least-32-characters",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    user = User(id="user-1", email="u@example.com", username="u", status=UserStatus.ACTIVE)
    refresh_token = jwt_service.issue_refresh_token(
        user_id=user.id, token_version=user.token_version
    )
    claims = jwt.decode(
        refresh_token, jwt_service.secret, algorithms=["HS256"], options={"verify_signature": False}
    )
    stored = RefreshToken(
        user_id=user.id,
        token_hash=jwt_service.hash_token(refresh_token),
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )
    refresh_repo = _FakeRefreshRepo(stored)
    user_repo = _FakeUserRepo(user)
    service = AuthService(
        uow_factory=lambda: _FakeUow(refresh_repo, user_repo),
        password_hasher=PasswordHasher(),
        token_codec=JwtTokenCodecAdapter(jwt_service),
    )

    refreshed_user, tokens = await service.refresh(refresh_token)

    assert refreshed_user.id == user.id
    assert tokens.access_token
    with pytest.raises(UnauthorizedError):
        await service.refresh(refresh_token)
