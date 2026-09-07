import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from redis.exceptions import RedisError

from app.application.ports.crypto import PasswordHashPort, TokenCodecError, TokenCodecPort
from app.application.security.authorization_context import authorization_scope
from app.domain.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.invitation import InvitationType
from app.domain.models.refresh_token import RefreshToken
from app.domain.models.scope import Principal
from app.domain.models.user import User, UserStatus
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

# Default account-lockout tuning. Kept as constructor defaults (not runtime
# policy) because AuthService has no runtime-policy reader injected; see the
# F13 TODO in ``AuthService.__init__``.
_DEFAULT_LOCKOUT_THRESHOLD = 5
_DEFAULT_LOCKOUT_WINDOW_SECONDS = 900  # rolling failure window: 15 minutes
_DEFAULT_LOCKOUT_BASE_SECONDS = 300  # first lock: 5 minutes
_DEFAULT_LOCKOUT_MAX_SECONDS = 3_600  # backoff cap: 1 hour


@dataclass(frozen=True)
class AuthTokenPair:
    access_token: str
    refresh_token: str


@runtime_checkable
class AuthThrottleStore(Protocol):
    """Per-identity login-failure counter and lockout marker.

    Implementations own the storage keys; identities handed in are already
    normalized (lowercased login identifier). ``register_failure`` returns the
    running failure count within the window so the service owns the lockout
    decision and exponential backoff.
    """

    async def register_failure(self, identity: str, *, window_seconds: int) -> int: ...

    async def arm_lock(self, identity: str, *, ttl_seconds: int) -> None: ...

    async def lock_ttl(self, identity: str) -> int: ...

    async def reset(self, identity: str) -> None: ...


class _RedisLike(Protocol):
    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, seconds: int) -> bool: ...

    async def set(self, name: str, value: str, *, ex: int) -> object: ...

    async def ttl(self, name: str) -> int: ...

    async def delete(self, *names: str) -> int: ...


class RedisAuthThrottleStore(AuthThrottleStore):
    """Redis-backed login throttle.

    Identities are hashed (never stored raw) to avoid persisting the login
    identifier / email as a Redis key, mirroring the credential fingerprinting
    the rate-limit middleware already does. Both keys carry a TTL so an idle
    identity self-heals: the failure counter expires with the rolling window and
    the lock expires with its (exponentially backed-off) duration.
    """

    _FAIL_PREFIX = "auth:login:fail:"
    _LOCK_PREFIX = "auth:login:lock:"

    def __init__(self, redis: _RedisLike) -> None:
        self._redis = redis

    @staticmethod
    def _fingerprint(identity: str) -> str:
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    async def register_failure(self, identity: str, *, window_seconds: int) -> int:
        key = f"{self._FAIL_PREFIX}{self._fingerprint(identity)}"
        try:
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, window_seconds)
        except (OSError, RedisError) as exc:
            # Fail open: an unreachable Redis must not block authentication.
            logger.warning("login throttle store unavailable on failure record: %s", exc)
            return 0
        return count

    async def arm_lock(self, identity: str, *, ttl_seconds: int) -> None:
        try:
            await self._redis.set(
                f"{self._LOCK_PREFIX}{self._fingerprint(identity)}",
                "1",
                ex=ttl_seconds,
            )
        except (OSError, RedisError) as exc:
            logger.warning("login throttle store unavailable on lock arm: %s", exc)

    async def lock_ttl(self, identity: str) -> int:
        try:
            ttl = int(await self._redis.ttl(f"{self._LOCK_PREFIX}{self._fingerprint(identity)}"))
        except (OSError, RedisError) as exc:
            logger.warning("login throttle store unavailable on lock check: %s", exc)
            return 0
        return max(0, ttl)

    async def reset(self, identity: str) -> None:
        fingerprint = self._fingerprint(identity)
        try:
            await self._redis.delete(
                f"{self._FAIL_PREFIX}{fingerprint}",
                f"{self._LOCK_PREFIX}{fingerprint}",
            )
        except (OSError, RedisError) as exc:
            logger.warning("login throttle store unavailable on reset: %s", exc)


class AuthService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        password_hasher: PasswordHashPort,
        token_codec: TokenCodecPort,
        *,
        throttle_store: AuthThrottleStore | None = None,
        lockout_threshold: int = _DEFAULT_LOCKOUT_THRESHOLD,
        lockout_window_seconds: int = _DEFAULT_LOCKOUT_WINDOW_SECONDS,
        lockout_base_seconds: int = _DEFAULT_LOCKOUT_BASE_SECONDS,
        lockout_max_seconds: int = _DEFAULT_LOCKOUT_MAX_SECONDS,
    ) -> None:
        # F13: composition/shared.py now injects a RedisAuthThrottleStore, so
        # the lockout is active in production; without a store it stays a no-op.
        # TODO(F13): lockout tuning (threshold/window/backoff) is still fixed at
        # construction from the module defaults below. Making it read live
        # TrafficPolicy would need new per-identity lockout fields on
        # TrafficPolicy plus request-time async reads via RuntimePolicyReader
        # (its only auth field today, ``auth_requests_per_minute``, is a
        # middleware rate-limit knob, not a lockout one).
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._token_codec = token_codec
        self._throttle_store = throttle_store
        self._lockout_threshold = lockout_threshold
        self._lockout_window_seconds = lockout_window_seconds
        self._lockout_base_seconds = lockout_base_seconds
        self._lockout_max_seconds = lockout_max_seconds

    async def register_with_invitation(
        self,
        *,
        invite_token: str,
        email: str,
        username: str,
        password: str,
    ) -> User:
        self.validate_password(password)
        with authorization_scope(AuthorizationContext.system("auth")):
            async with self._uow_factory() as uow:
                invitation = await uow.invitation.get_by_token(invite_token)
                if not invitation or invitation.type != InvitationType.PLATFORM:
                    raise BadRequestError("邀请链接无效")
                if invitation.accepted_at is not None:
                    raise BadRequestError("邀请链接已被使用")
                if invitation.expires_at < datetime.now(UTC):
                    raise BadRequestError("邀请链接已过期", error_key="errors.inviteExpired")
                normalized_email = email.strip().lower()
                if invitation.email and invitation.email.strip().lower() != normalized_email:
                    raise BadRequestError("注册邮箱与邀请不匹配")
                if await uow.user.get_by_email(normalized_email):
                    raise ConflictError("邮箱已注册")
                if await uow.user.get_by_username(username):
                    raise ConflictError("用户名已存在")
                user = User(
                    email=normalized_email,
                    username=username,
                    password_hash=self._password_hasher.hash(password),
                )
                await uow.user.save(user)
                invitation.accepted_at = datetime.now(UTC)
                invitation.accepted_user_id = user.id
                await uow.invitation.save(invitation)
                await uow.commit()
                return user

    async def login(
        self,
        *,
        email_or_username: str,
        password: str,
        user_agent: str = "",
        ip_address: str = "",
    ) -> tuple[User, AuthTokenPair]:
        identifier = email_or_username.strip()
        # Key the lockout on the normalized login input (not the resolved user)
        # so unknown identifiers and real accounts are counted and rejected
        # identically — that keeps the lockout from leaking account existence.
        throttle_identity = identifier.lower()
        await self._reject_if_locked(throttle_identity)
        with authorization_scope(AuthorizationContext.system("auth")):
            async with self._uow_factory() as uow:
                user: User | None
                if "@" in identifier:
                    user = await uow.user.get_by_email(identifier.lower())
                else:
                    user = await uow.user.get_by_username(identifier)
                if not user or not self._password_hasher.verify(password, user.password_hash):
                    await self._record_login_failure(throttle_identity)
                    raise UnauthorizedError("账号或密码错误", error_key="errors.invalidCredentials")
                if user.status != UserStatus.ACTIVE:
                    raise UnauthorizedError("账号已被禁用")
                user.last_login_at = datetime.now(UTC)
                await uow.user.update_last_login(user.id, user.last_login_at)
                tokens = await self._issue_tokens(
                    uow, user, user_agent=user_agent, ip_address=ip_address
                )
                await uow.audit.add(
                    AuditLog(
                        actor_user_id=user.id,
                        actor_ip=ip_address,
                        action="login",
                        resource_type="user",
                        resource_id=user.id,
                        metadata={"auth_method": "password"},
                    )
                )
                await uow.commit()
                await self._clear_login_failures(throttle_identity)
                return user, tokens

    async def _reject_if_locked(self, identity: str) -> None:
        """Reject a locked identity before any password check, so a correct
        password is still refused while the lockout is in effect. The error is
        deliberately generic ("try again later") and does not distinguish
        "locked" from "wrong password" beyond the dedicated error key."""
        if self._throttle_store is None:
            return
        try:
            ttl = await self._throttle_store.lock_ttl(identity)
        except (OSError, RuntimeError, ValueError) as exc:
            # Fail open: a throttle-store outage must not lock everyone out.
            logger.warning("login throttle store unavailable on lock check: %s", exc)
            return
        if ttl > 0:
            raise UnauthorizedError(
                "登录尝试过于频繁，请稍后再试",
                error_key="errors.tooManyLoginAttempts",
            )

    async def _record_login_failure(self, identity: str) -> None:
        """Count a failed attempt and arm an exponentially backed-off lock once
        the threshold is crossed."""
        if self._throttle_store is None:
            return
        try:
            count = await self._throttle_store.register_failure(
                identity,
                window_seconds=self._lockout_window_seconds,
            )
            if count >= self._lockout_threshold:
                excess = count - self._lockout_threshold
                ttl = min(
                    self._lockout_base_seconds * (2**excess),
                    self._lockout_max_seconds,
                )
                await self._throttle_store.arm_lock(identity, ttl_seconds=ttl)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("login throttle store unavailable on failure record: %s", exc)

    async def _clear_login_failures(self, identity: str) -> None:
        if self._throttle_store is None:
            return
        try:
            await self._throttle_store.reset(identity)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("login throttle store unavailable on reset: %s", exc)

    @staticmethod
    def validate_password(password: str) -> None:
        if not 8 <= len(password) <= 128 or not password.strip():
            raise BadRequestError("密码须为 8–128 个字符，且不能全为空白")

    async def change_password(
        self,
        *,
        principal: Principal,
        current_password: str,
        new_password: str,
        ip_address: str = "",
    ) -> None:
        await self._replace_password(
            principal=principal,
            user_id=principal.user_id,
            current_password=current_password,
            new_password=new_password,
            admin_reset=False,
            ip_address=ip_address,
        )

    async def reset_password(
        self,
        *,
        principal: Principal,
        user_id: str,
        new_password: str,
        ip_address: str = "",
    ) -> None:
        if not principal.is_admin:
            raise ForbiddenError("需要管理员权限", error_key="errors.adminRequired")
        await self._replace_password(
            principal=principal,
            user_id=user_id,
            current_password=None,
            new_password=new_password,
            admin_reset=True,
            ip_address=ip_address,
        )

    async def _replace_password(
        self,
        *,
        principal: Principal,
        user_id: str,
        current_password: str | None,
        new_password: str,
        admin_reset: bool,
        ip_address: str,
    ) -> None:
        self.validate_password(new_password)
        with authorization_scope(AuthorizationContext.system("auth-password")):
            async with self._uow_factory() as uow:
                actor = await uow.user.get_by_id(principal.user_id)
                if (
                    not actor
                    or not actor.is_active
                    or actor.token_version != principal.token_version
                ):
                    raise UnauthorizedError("账号或会话已失效")
                if admin_reset and not actor.is_admin:
                    raise ForbiddenError("需要管理员权限", error_key="errors.adminRequired")
                user = actor if user_id == actor.id else await uow.user.get_by_id(user_id)
                if not user:
                    raise NotFoundError("用户不存在")
                if not admin_reset and (
                    not user.password_hash
                    or not self._password_hasher.verify(current_password or "", user.password_hash)
                ):
                    raise UnauthorizedError("当前密码错误；无本地密码请联系管理员重置")
                if user.password_hash and self._password_hasher.verify(
                    new_password, user.password_hash
                ):
                    raise BadRequestError("新密码不能与当前密码相同")
                changed = await uow.user.replace_password(
                    user.id,
                    expected_hash=user.password_hash,
                    expected_version=user.token_version,
                    password_hash=self._password_hasher.hash(new_password),
                )
                if not changed:
                    raise ConflictError("账号凭据已变更，请重新登录后重试")
                await uow.refresh_token.revoke_all_for_user(user.id)
                await uow.audit.add(
                    AuditLog(
                        actor_user_id=actor.id,
                        actor_ip=ip_address,
                        action="admin.user.password.reset"
                        if admin_reset
                        else "auth.password.change",
                        resource_type="user",
                        resource_id=user.id,
                        metadata={"sessions_revoked": True},
                    )
                )
                await uow.commit()
        await self._clear_login_failures(user.email.lower())
        await self._clear_login_failures(user.username.lower())

    async def refresh(
        self, refresh_token: str, *, user_agent: str = "", ip_address: str = ""
    ) -> tuple[User, AuthTokenPair]:
        try:
            claims = self._token_codec.decode(refresh_token, expected_type="refresh")
        except TokenCodecError as exc:
            raise UnauthorizedError("刷新令牌无效") from exc
        user_id = str(claims.get("sub") or "")
        token_hash = self._token_codec.hash_token(refresh_token)
        with authorization_scope(AuthorizationContext.system("auth")):
            async with self._uow_factory() as uow:
                stored = await uow.refresh_token.consume_by_hash(token_hash)
                if not stored or stored.user_id != user_id or stored.expires_at < datetime.now(UTC):
                    raise UnauthorizedError("刷新令牌已失效")
                user = await uow.user.get_by_id(user_id)
                if not user or user.status != UserStatus.ACTIVE:
                    raise UnauthorizedError("账号不可用")
                if int(claims.get("ver", -1)) != user.token_version:
                    raise UnauthorizedError("令牌版本已失效")
                tokens = await self._issue_tokens(
                    uow, user, user_agent=user_agent, ip_address=ip_address
                )
                await uow.commit()
                return user, tokens

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        token_hash = self._token_codec.hash_token(refresh_token)
        try:
            claims = self._token_codec.decode(
                refresh_token,
                expected_type="refresh",
            )
            user_id = str(claims.get("sub") or "")
        except TokenCodecError:
            user_id = ""
        with authorization_scope(AuthorizationContext.system("auth")):
            async with self._uow_factory() as uow:
                await uow.refresh_token.revoke_by_hash(token_hash)
                if user_id:
                    await uow.audit.add(
                        AuditLog(
                            actor_user_id=user_id,
                            action="logout",
                            resource_type="user",
                            resource_id=user_id,
                        )
                    )
                await uow.commit()

    async def issue_tokens_for_user(
        self,
        user: User,
        *,
        user_agent: str = "",
        ip_address: str = "",
        audit_action: str | None = None,
    ) -> AuthTokenPair:
        with authorization_scope(AuthorizationContext.system("auth")):
            async with self._uow_factory() as uow:
                tokens = await self._issue_tokens(
                    uow,
                    user,
                    user_agent=user_agent,
                    ip_address=ip_address,
                )
                if audit_action:
                    await uow.audit.add(
                        AuditLog(
                            actor_user_id=user.id,
                            actor_ip=ip_address,
                            action=audit_action,
                            resource_type="user",
                            resource_id=user.id,
                        )
                    )
                await uow.commit()
                return tokens

    async def _issue_tokens(
        self,
        uow: IUnitOfWork,
        user: User,
        *,
        user_agent: str,
        ip_address: str,
    ) -> AuthTokenPair:
        access_token = self._token_codec.issue_access_token(
            user_id=user.id,
            role=user.global_role.value,
            token_version=user.token_version,
        )
        refresh_token = self._token_codec.issue_refresh_token(
            user_id=user.id,
            token_version=user.token_version,
        )
        await uow.refresh_token.save(
            RefreshToken(
                user_id=user.id,
                token_hash=self._token_codec.hash_token(refresh_token),
                expires_at=datetime.fromtimestamp(
                    int(
                        self._token_codec.decode(
                            refresh_token,
                            expected_type="refresh",
                        )["exp"]
                    ),
                    UTC,
                ),
                user_agent=user_agent,
                ip_address=ip_address,
            )
        )
        return AuthTokenPair(access_token=access_token, refresh_token=refresh_token)
