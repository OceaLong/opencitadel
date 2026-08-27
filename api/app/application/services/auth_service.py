from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.ports.crypto import PasswordHashPort, TokenCodecError, TokenCodecPort
from app.domain.errors import BadRequestError, ConflictError, UnauthorizedError
from app.domain.models.audit_log import AuditLog
from app.domain.models.invitation import InvitationType
from app.domain.models.refresh_token import RefreshToken
from app.domain.models.user import User, UserStatus
from app.domain.repositories.uow import IUnitOfWork


@dataclass(frozen=True)
class AuthTokenPair:
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        password_hasher: PasswordHashPort,
        token_codec: TokenCodecPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._token_codec = token_codec

    async def register_with_invitation(
        self,
        *,
        invite_token: str,
        email: str,
        username: str,
        password: str,
    ) -> User:
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
        async with self._uow_factory() as uow:
            identifier = email_or_username.strip()
            user: User | None
            if "@" in identifier:
                user = await uow.user.get_by_email(identifier.lower())
            else:
                user = await uow.user.get_by_username(identifier)
            if not user or not self._password_hasher.verify(password, user.password_hash):
                raise UnauthorizedError("账号或密码错误", error_key="errors.invalidCredentials")
            if user.status != UserStatus.ACTIVE:
                raise UnauthorizedError("账号已被禁用")
            user.last_login_at = datetime.now(UTC)
            await uow.user.save(user)
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
            return user, tokens

    async def refresh(
        self, refresh_token: str, *, user_agent: str = "", ip_address: str = ""
    ) -> tuple[User, AuthTokenPair]:
        try:
            claims = self._token_codec.decode(refresh_token, expected_type="refresh")
        except TokenCodecError as exc:
            raise UnauthorizedError("刷新令牌无效") from exc
        user_id = str(claims.get("sub") or "")
        token_hash = self._token_codec.hash_token(refresh_token)
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
