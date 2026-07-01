#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.domain.models.scope import Principal
from app.domain.models.user import UserStatus
from app.infrastructure.security.cookie import ACCESS_COOKIE
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_context import set_principal

logger = logging.getLogger(__name__)


class AuthContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwt_service: JwtService) -> None:
        super().__init__(app)
        self.jwt_service = jwt_service

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = request.cookies.get(ACCESS_COOKIE)
        context_token = set_principal(None)
        try:
            if token:
                principal = await self._principal_from_token(token)
                set_principal(principal)
            return await call_next(request)
        finally:
            set_principal(None)
            try:
                context_token.var.reset(context_token)
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
