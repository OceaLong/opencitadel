"""Greenfield authentication against identity-owned tables only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.crypto import PasswordHashPort, TokenCodecError, TokenCodecPort
from app.domain.errors import ConflictError, UnauthorizedError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import InvitationORM, OAuthIdentityORM, RefreshTokenORM, TeamMemberORM, UserORM
from .services import _append_audit


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str


def user_view(user: UserORM) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "displayName": user.display_name,
        "globalRole": user.global_role,
        "status": "active" if user.enabled else "disabled",
        "createdAt": user.created_at.isoformat(),
        "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
    }


class PostgresAuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        password_hasher: PasswordHashPort,
        token_codec: TokenCodecPort,
        refresh_ttl_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._password_hasher = password_hasher
        self._token_codec = token_codec
        self._refresh_ttl_seconds = refresh_ttl_seconds

    @property
    def token_codec(self) -> TokenCodecPort:
        return self._token_codec

    async def bootstrap_admin(self, *, email: str, password: str) -> None:
        if not email.strip() or not password:
            return
        now = datetime.now(UTC)
        async with self._system_session("bootstrap") as session:
            existing = await session.scalar(
                select(UserORM).where(UserORM.email == email.strip().lower())
            )
            if existing is not None:
                return
            stem = email.split("@", 1)[0] or "admin"
            session.add(
                UserORM(
                    id=str(uuid4()),
                    email=email.strip().lower(),
                    username=stem,
                    password_hash=self._password_hasher.hash(password),
                    display_name="Administrator",
                    global_role=GlobalRole.ADMIN.value,
                    enabled=True,
                    token_version=0,
                    created_at=now,
                    updated_at=now,
                    last_login_at=None,
                )
            )

    async def login(self, identifier: str, password: str) -> tuple[dict[str, object], AuthTokens]:
        normalized = identifier.strip()
        async with self._system_session("login") as session:
            user = await session.scalar(
                select(UserORM).where(
                    or_(
                        UserORM.email == normalized.lower(),
                        UserORM.username == normalized,
                    )
                )
            )
            if (
                user is None
                or not user.enabled
                or not self._password_hasher.verify(password, user.password_hash)
            ):
                raise UnauthorizedError("账号或密码错误", error_key="errors.invalidCredentials")
            user.last_login_at = datetime.now(UTC)
            user.updated_at = user.last_login_at
            tokens = await self._issue(session, user)
            return user_view(user), tokens

    async def register(
        self,
        *,
        invitation_token: str,
        email: str,
        username: str,
        password: str,
        display_name: str,
    ) -> tuple[dict[str, object], AuthTokens]:
        """Create a local account only through a valid team invitation."""

        import hashlib

        normalized_email = email.strip().lower()
        normalized_username = username.strip()
        digest = hashlib.sha256(invitation_token.encode()).hexdigest()
        now = datetime.now(UTC)
        async with self._system_session("registration") as session:
            invitation = await session.scalar(
                select(InvitationORM).where(InvitationORM.token_digest == digest).with_for_update()
            )
            if (
                invitation is None
                or invitation.accepted_at is not None
                or invitation.expires_at <= now
                or invitation.email != normalized_email
            ):
                raise ConflictError("Invitation is invalid or expired")
            duplicate = await session.scalar(
                select(UserORM.id).where(
                    or_(
                        UserORM.email == normalized_email,
                        UserORM.username == normalized_username,
                    )
                )
            )
            if duplicate is not None:
                raise ConflictError("Email or username is already registered")
            user = UserORM(
                id=str(uuid4()),
                email=normalized_email,
                username=normalized_username,
                password_hash=self._password_hasher.hash(password),
                display_name=display_name.strip() or normalized_username,
                global_role=GlobalRole.USER.value,
                enabled=True,
                token_version=0,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            session.add(user)
            await session.flush()
            session.add(
                TeamMemberORM(
                    team_id=invitation.team_id,
                    user_id=user.id,
                    role=invitation.role,
                    joined_at=now,
                )
            )
            invitation.accepted_at = now
            await _append_audit(
                session,
                shard_key=f"team:{invitation.team_id}",
                actor_user_id=user.id,
                action="invitation.accepted",
                resource_type="team",
                resource_id=invitation.team_id,
                metadata={"invitationId": str(invitation.id)},
                now=now,
            )
            tokens = await self._issue(session, user)
            return user_view(user), tokens

    async def refresh(self, refresh_token: str) -> tuple[dict[str, object], AuthTokens]:
        try:
            claims = self._token_codec.decode(refresh_token, expected_type="refresh")
        except Exception as exc:
            raise UnauthorizedError("刷新令牌无效") from exc
        digest = self._token_codec.hash_token(refresh_token)
        now = datetime.now(UTC)
        async with self._system_session("refresh") as session:
            stored = await session.scalar(
                select(RefreshTokenORM)
                .where(
                    RefreshTokenORM.token_digest == digest,
                    RefreshTokenORM.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if stored is None or stored.expires_at <= now:
                raise UnauthorizedError("刷新令牌已失效")
            user = await session.get(UserORM, stored.user_id)
            if (
                user is None
                or not user.enabled
                or str(claims.get("sub")) != user.id
                or int(claims.get("ver", -1)) != user.token_version
            ):
                raise UnauthorizedError("账号或令牌版本不可用")
            stored.revoked_at = now
            tokens = await self._issue(session, user)
            return user_view(user), tokens

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        async with self._system_session("logout") as session:
            await session.execute(
                update(RefreshTokenORM)
                .where(
                    RefreshTokenORM.token_digest == self._token_codec.hash_token(refresh_token),
                    RefreshTokenORM.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )

    async def get_user(self, user_id: str) -> dict[str, object] | None:
        async with self._system_session("identity-query") as session:
            user = await session.get(UserORM, user_id)
            return user_view(user) if user is not None and user.enabled else None

    async def oauth_authenticate(
        self,
        *,
        provider: str,
        subject: str,
        email: str,
        display_name: str,
    ) -> tuple[dict[str, object], AuthTokens]:
        normalized_email = email.strip().lower()
        if not normalized_email or not subject:
            raise UnauthorizedError("OAuth identity is incomplete")
        now = datetime.now(UTC)
        async with self._system_session("oauth") as session:
            identity = await session.scalar(
                select(OAuthIdentityORM).where(
                    OAuthIdentityORM.provider == provider,
                    OAuthIdentityORM.subject == subject,
                )
            )
            user = await session.get(UserORM, identity.user_id) if identity else None
            if user is None:
                user = await session.scalar(
                    select(UserORM).where(UserORM.email == normalized_email)
                )
            if user is None:
                base = normalized_email.split("@", 1)[0] or "user"
                username = base
                suffix = 0
                while await session.scalar(select(UserORM.id).where(UserORM.username == username)):
                    suffix += 1
                    username = f"{base}-{suffix}"
                user = UserORM(
                    id=str(uuid4()),
                    email=normalized_email,
                    username=username,
                    password_hash=None,
                    display_name=display_name or username,
                    global_role=GlobalRole.USER.value,
                    enabled=True,
                    token_version=0,
                    created_at=now,
                    updated_at=now,
                    last_login_at=now,
                )
                session.add(user)
                await session.flush()
            if identity is None:
                session.add(
                    OAuthIdentityORM(
                        id=uuid4(),
                        user_id=user.id,
                        provider=provider,
                        subject=subject,
                        created_at=now,
                    )
                )
            if not user.enabled:
                raise UnauthorizedError("账号已被禁用")
            user.last_login_at = now
            user.updated_at = now
            tokens = await self._issue(session, user)
            return user_view(user), tokens

    async def principal_from_access(self, token: str) -> Principal | None:
        try:
            claims = self._token_codec.decode(token, expected_type="access")
        except (TokenCodecError, ValueError):
            return None
        user_id = str(claims.get("sub") or "")
        async with self._system_session("auth-context") as session:
            user = await session.get(UserORM, user_id)
            if user is None or not user.enabled or int(claims.get("ver", -1)) != user.token_version:
                return None
            memberships = (
                await session.scalars(select(TeamMemberORM).where(TeamMemberORM.user_id == user_id))
            ).all()
        return Principal(
            user_id=user.id,
            global_role=GlobalRole(user.global_role),
            token_version=user.token_version,
            team_roles={item.team_id: TeamRole(item.role) for item in memberships},
        )

    async def _issue(self, session: AsyncSession, user: UserORM) -> AuthTokens:
        access = self._token_codec.issue_access_token(
            user_id=user.id,
            role=user.global_role,
            token_version=user.token_version,
        )
        refresh = self._token_codec.issue_refresh_token(
            user_id=user.id,
            token_version=user.token_version,
        )
        now = datetime.now(UTC)
        session.add(
            RefreshTokenORM(
                id=uuid4(),
                user_id=user.id,
                token_digest=self._token_codec.hash_token(refresh),
                expires_at=now + timedelta(seconds=self._refresh_ttl_seconds),
                revoked_at=None,
                created_at=now,
            )
        )
        return AuthTokens(access_token=access, refresh_token=refresh)

    def _system_session(self, actor: str):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def open_session():
            async with self._session_factory() as session, session.begin():
                await bind_context(session, AuthorizationContext.system(actor))
                yield session

        return open_session()


__all__ = ["AuthTokens", "PostgresAuthService", "user_view"]
