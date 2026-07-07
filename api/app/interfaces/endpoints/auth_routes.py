#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response as StarletteResponse
from starlette.responses import RedirectResponse

from app.application.errors.exceptions import BadRequestError, UnauthorizedError
from app.application.services.auth_service import AuthService
from app.domain.models.invitation import InvitationType
from app.domain.models.oauth_identity import OAuthIdentity
from app.domain.models.team import TeamMember, TeamRole
from app.domain.models.user import User
from app.domain.utils.safe_redirect import resolve_safe_redirect_path
from app.infrastructure.security.cookie import AuthCookieManager, REFRESH_COOKIE
from app.interfaces.auth_dependencies import get_current_principal, verify_csrf
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from app.interfaces.service_dependencies import get_auth_service, get_cookie_manager
from app.infrastructure.storage.postgres import get_uow
from core.config import get_settings

router = APIRouter(prefix="/auth", tags=["认证模块"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


@router.post("/register", response_model=ApiResponse[UserResponse])
async def register(
        response: StarletteResponse,
        request: Request,
        body: RegisterRequest,
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
) -> ApiResponse[UserResponse]:
    user = await auth_service.register_with_invitation(
        invite_token=body.invite_token,
        email=body.email,
        username=body.username,
        password=body.password,
    )
    user, tokens = await auth_service.login(
        email_or_username=user.email,
        password=body.password,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=_client_ip(request),
    )
    cookie_manager.set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return ApiResponse.success(UserResponse.from_domain(user))


@router.post("/login", response_model=ApiResponse[UserResponse])
async def login(
        response: StarletteResponse,
        request: Request,
        body: LoginRequest,
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
) -> ApiResponse[UserResponse]:
    user, tokens = await auth_service.login(
        email_or_username=body.email_or_username,
        password=body.password,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=_client_ip(request),
    )
    cookie_manager.set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return ApiResponse.success(UserResponse.from_domain(user))


@router.post("/refresh", response_model=ApiResponse[UserResponse])
async def refresh(
        response: StarletteResponse,
        request: Request,
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
) -> ApiResponse[UserResponse]:
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise UnauthorizedError("缺少刷新令牌")
    user, tokens = await auth_service.refresh(
        refresh_token,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=_client_ip(request),
    )
    cookie_manager.set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return ApiResponse.success(UserResponse.from_domain(user))


@router.post("/logout", response_model=ApiResponse[dict], dependencies=[Depends(verify_csrf)])
async def logout(
        response: StarletteResponse,
        request: Request,
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
) -> ApiResponse[dict]:
    await auth_service.logout(request.cookies.get(REFRESH_COOKIE))
    cookie_manager.clear_auth_cookies(response)
    return ApiResponse.success()


@router.get("/me", response_model=ApiResponse[UserResponse])
async def me(principal=Depends(get_current_principal)) -> ApiResponse[UserResponse]:
    async with get_uow() as uow:
        user = await uow.user.get_by_id(principal.user_id)
    if not user:
        raise UnauthorizedError()
    return ApiResponse.success(UserResponse.from_domain(user))


@router.get("/oauth/{provider}/login")
async def oauth_login(
        provider: str,
        request: Request,
        redirect: str = Query(default=""),
        team_invite_token: str = Query(default=""),
):
    from app.container import get_api_container

    client = get_api_container().oauth_clients().get(provider)
    if client is None:
        raise BadRequestError("OAuth 提供商未启用")
    request.session["oauth_redirect"] = resolve_safe_redirect_path(redirect)
    request.session["oauth_team_invite_token"] = (team_invite_token or "").strip()
    redirect_uri = f"{get_settings().oauth_redirect_base}/{provider}/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
        provider: str,
        request: Request,
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
):
    from app.container import get_api_container

    client = get_api_container().oauth_clients().get(provider)
    if client is None:
        raise BadRequestError("OAuth 提供商未启用")
    token = await client.authorize_access_token(request)
    profile = await _load_oauth_profile(provider, client, token)
    email = (profile.get("email") or "").strip().lower()
    if not email or not profile.get("email_verified", False):
        raise BadRequestError("OAuth 邮箱未验证，无法登录")
    provider_user_id = str(profile.get("sub") or profile.get("id") or "")
    if not provider_user_id:
        raise BadRequestError("OAuth 用户标识缺失")

    async with get_uow() as uow:
        identity = await uow.oauth_identity.get_by_provider_identity(provider, provider_user_id)
        user = await uow.user.get_by_id(identity.user_id) if identity else await uow.user.get_by_email(email)
        team_invite_token = (request.session.pop("oauth_team_invite_token", "") or "").strip()
        oauth_redirect = resolve_safe_redirect_path(request.session.pop("oauth_redirect", ""))

        if not user:
            invitation = await _resolve_oauth_registration_invitation(
                uow,
                email=email,
                team_invite_token=team_invite_token,
            )
            if not invitation:
                raise BadRequestError("该邮箱尚未收到平台邀请")
            username = email.split("@", 1)[0] or "user"
            if await uow.user.get_by_username(username):
                username = f"{username}-{provider_user_id[-6:]}"
            user = User(
                email=email,
                username=username,
                display_name=profile.get("name") or username,
                avatar_url=profile.get("picture") or profile.get("avatar_url") or "",
            )
            await uow.user.save(user)
            invitation.accepted_at = datetime.now()
            invitation.accepted_user_id = user.id
            await uow.invitation.save(invitation)
            if invitation.type == InvitationType.TEAM and invitation.team_id:
                existing_member = await uow.team.get_member(invitation.team_id, user.id)
                if not existing_member:
                    await uow.team.add_member(
                        TeamMember(
                            team_id=invitation.team_id,
                            user_id=user.id,
                            role=invitation.team_role or TeamRole.MEMBER,
                        )
                    )
        elif team_invite_token:
            team_invitation = await uow.invitation.get_by_token(team_invite_token)
            if (
                    team_invitation
                    and team_invitation.type == InvitationType.TEAM
                    and team_invitation.team_id
                    and not team_invitation.accepted
                    and team_invitation.expires_at >= datetime.now()
            ):
                if team_invitation.email and team_invitation.email.strip().lower() != email:
                    raise BadRequestError("邀请邮箱与 OAuth 账号不匹配")
                existing_member = await uow.team.get_member(team_invitation.team_id, user.id)
                if not existing_member:
                    await uow.team.add_member(
                        TeamMember(
                            team_id=team_invitation.team_id,
                            user_id=user.id,
                            role=team_invitation.team_role or TeamRole.MEMBER,
                        )
                    )
                team_invitation.accepted_at = datetime.now()
                team_invitation.accepted_user_id = user.id
                await uow.invitation.save(team_invitation)
        if not identity:
            await uow.oauth_identity.save(
                OAuthIdentity(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                    email_verified=True,
                )
            )

    tokens = await auth_service.issue_tokens_for_user(
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=_client_ip(request),
    )
    response = RedirectResponse(f"{get_settings().frontend_base_url.rstrip('/')}{oauth_redirect}")
    cookie_manager.set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return response


async def _resolve_oauth_registration_invitation(uow, *, email: str, team_invite_token: str):
    if team_invite_token:
        invitation = await uow.invitation.get_by_token(team_invite_token)
        if (
                invitation
                and invitation.type == InvitationType.TEAM
                and invitation.team_id
                and not invitation.accepted
                and invitation.expires_at >= datetime.now()
                and invitation.email
                and invitation.email.strip().lower() == email
        ):
            return invitation
    invitations = await uow.invitation.list(invitation_type=InvitationType.PLATFORM, limit=500)
    return next(
        (
            item for item in invitations
            if item.email and item.email.strip().lower() == email and not item.accepted
        ),
        None,
    )


async def _load_oauth_profile(provider: str, client, token: dict) -> dict:
    if provider == "google":
        userinfo = token.get("userinfo") or await client.parse_id_token(token)
        return {
            "sub": userinfo.get("sub"),
            "email": userinfo.get("email"),
            "email_verified": bool(userinfo.get("email_verified")),
            "name": userinfo.get("name"),
            "picture": userinfo.get("picture"),
        }
    if provider == "github":
        user_resp = await client.get("user", token=token)
        user = user_resp.json()
        email = user.get("email")
        verified = bool(email)
        if not email:
            emails_resp = await client.get("user/emails", token=token)
            for item in emails_resp.json():
                if item.get("primary") and item.get("verified"):
                    email = item.get("email")
                    verified = True
                    break
        return {
            "id": user.get("id"),
            "email": email,
            "email_verified": verified,
            "name": user.get("name") or user.get("login"),
            "avatar_url": user.get("avatar_url"),
        }
    raise BadRequestError("不支持的 OAuth 提供商")
