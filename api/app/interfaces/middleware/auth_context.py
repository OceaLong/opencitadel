#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.application.security.authorization_context import (
    reset_authorization_context,
    set_authorization_context,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.models.user import UserStatus
from app.infrastructure.security.cookie import ACCESS_COOKIE
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_context import set_principal
from app.infrastructure.observability.logging_context import get_request_id

logger = logging.getLogger(__name__)


class AuthContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwt_service: JwtService) -> None:
        super().__init__(app)
        self.jwt_service = jwt_service

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = request.cookies.get(ACCESS_COOKIE)
        principal_token = set_principal(None)
        authorization_token = set_authorization_context(AuthorizationContext.anonymous())
        try:
            if token:
                principal = await self._principal_from_token(token)
                set_principal(principal)
                if principal is not None:
                    set_authorization_context(
                        AuthorizationContext.for_principal(
                            principal,
                            request_id=get_request_id() or "",
                        )
                    )
            return await call_next(request)
        finally:
            try:
                reset_authorization_context(authorization_token)
            except Exception:
                pass
            try:
                principal_token.var.reset(principal_token)
            except Exception:
                pass

    async def _principal_from_token(self, token: str) -> Principal | None:
        try:
            claims = self.jwt_service.decode(token, expected_type="access")
        except jwt.PyJWTError:
            return None
        user_id = str(claims.get("sub") or "")
        if not user_id:
            return None
        try:
            async with get_uow() as uow:
                user = await uow.user.get_by_id(user_id)
                if not user or user.status != UserStatus.ACTIVE:
                    return None
                if int(claims.get("ver", -1)) != user.token_version:
                    return None
                teams = await uow.team.list_for_user(user_id)
                team_roles = {}
                for team in teams:
                    member = await uow.team.get_member(team.id, user_id)
                    if member:
                        team_roles[team.id] = member.role
                return Principal(
                    user_id=user.id,
                    global_role=user.global_role,
                    token_version=user.token_version,
                    team_roles=team_roles,
                )
        except Exception as exc:
            logger.warning("auth context lookup failed: %s", exc)
            return None
