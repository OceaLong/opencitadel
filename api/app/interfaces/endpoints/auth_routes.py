"""Local and OAuth authentication for the greenfield identity context."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Response as HttpResponse
from pydantic import BaseModel, Field
from starlette.responses import RedirectResponse

from app.application.ports.crypto import REFRESH_COOKIE, read_host_cookie
from app.contexts.identity.runtime import IdentityRuntime
from app.domain.errors import BadRequestError, UnauthorizedError
from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.schemas import Response
from app.interfaces.service_dependencies import get_identity_runtime

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email_or_username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class RegisterRequest(BaseModel):
    invitation_token: str = Field(min_length=32, max_length=512)
    email: str = Field(min_length=3, max_length=320)
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=255)


def _set_cookies(response: HttpResponse, runtime: IdentityRuntime, tokens) -> None:
    runtime.cookies.set_auth_cookies(
        response,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    response: HttpResponse,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    user, tokens = await runtime.auth.login(body.email_or_username, body.password)
    _set_cookies(response, runtime, tokens)
    return Response.success(user)


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    response: HttpResponse,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    user, tokens = await runtime.auth.register(
        invitation_token=body.invitation_token,
        email=body.email,
        username=body.username,
        password=body.password,
        display_name=body.display_name,
    )
    _set_cookies(response, runtime, tokens)
    return Response.success(user)


@router.post("/refresh")
async def refresh(
    request: Request,
    response: HttpResponse,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    token = read_host_cookie(request.cookies, REFRESH_COOKIE)
    if not token:
        raise UnauthorizedError("缺少刷新令牌")
    user, tokens = await runtime.auth.refresh(token)
    _set_cookies(response, runtime, tokens)
    return Response.success(user)


@router.post("/logout")
async def logout(
    request: Request,
    response: HttpResponse,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    await runtime.auth.logout(read_host_cookie(request.cookies, REFRESH_COOKIE))
    runtime.cookies.clear_auth_cookies(response)
    return Response.success({"loggedOut": True})


@router.get("/me")
async def me(
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    user = await runtime.auth.get_user(principal.user_id)
    if user is None:
        raise UnauthorizedError()
    return Response.success(user)


@router.get("/oauth/providers")
async def oauth_providers(runtime: IdentityRuntime = Depends(get_identity_runtime)):
    return Response.success(runtime.oauth.enabled_providers())


@router.get("/oauth/{provider}/login")
async def oauth_login(
    provider: str,
    request: Request,
    redirect: str = Query(default="/"),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    client = runtime.oauth.get(provider)
    if client is None:
        raise BadRequestError("OAuth provider is not enabled")
    request.session["oauth_redirect"] = redirect if redirect.startswith("/") else "/"
    uri = f"{runtime.application_urls.oauth_redirect_base}/{provider}/callback"
    return await client.authorize_redirect(request, uri)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    client = runtime.oauth.get(provider)
    if client is None:
        raise BadRequestError("OAuth provider is not enabled")
    token = await client.authorize_access_token(request)
    profile = await _oauth_profile(provider, client, token)
    if not profile["verified"]:
        raise BadRequestError("OAuth email must be verified")
    user, tokens = await runtime.auth.oauth_authenticate(
        provider=provider,
        subject=profile["subject"],
        email=profile["email"],
        display_name=profile["name"],
    )
    del user
    target = request.session.pop("oauth_redirect", "/")
    response = RedirectResponse(f"{runtime.application_urls.frontend_base_url.rstrip('/')}{target}")
    _set_cookies(response, runtime, tokens)
    return response


async def _oauth_profile(provider: str, client, token: dict) -> dict[str, object]:
    if provider == "google":
        value = dict(token.get("userinfo") or {})
        return {
            "subject": str(value.get("sub") or ""),
            "email": str(value.get("email") or ""),
            "verified": bool(value.get("email_verified")),
            "name": str(value.get("name") or ""),
        }
    if provider == "github":
        emails = (await client.get("user/emails", token=token)).json()
        verified = next(
            (item for item in emails if item.get("primary") and item.get("verified")),
            {},
        )
        value = (await client.get("user", token=token)).json()
        return {
            "subject": str(value.get("id") or ""),
            "email": str(verified.get("email") or ""),
            "verified": bool(verified),
            "name": str(value.get("name") or value.get("login") or ""),
        }
    raise BadRequestError("Unsupported OAuth provider")
