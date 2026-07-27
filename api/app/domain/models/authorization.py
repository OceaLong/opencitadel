#!/usr/bin/env python
# -*- coding: utf-8 -*-
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole


class AuthorizationMode(str, Enum):
    ANONYMOUS = "anonymous"
    USER = "user"
    SYSTEM = "system"


class AuthorizationContext(BaseModel):
    """Immutable authorization identity propagated into every database transaction."""

    model_config = ConfigDict(frozen=True)

    mode: AuthorizationMode
    principal: Optional[Principal] = None
    scope: Optional[OwnerScope] = None
    request_id: str = ""
    system_actor: str = ""

    @classmethod
    def anonymous(cls) -> "AuthorizationContext":
        return cls(mode=AuthorizationMode.ANONYMOUS)

    @classmethod
    def for_principal(
        cls,
        principal: Principal,
        *,
        scope: Optional[OwnerScope] = None,
        request_id: str = "",
    ) -> "AuthorizationContext":
        resolved_scope = scope or OwnerScope.personal(principal.user_id)
        if resolved_scope.user_id != principal.user_id:
            raise ValueError("授权作用域与当前主体不匹配")
        if resolved_scope.team_id and resolved_scope.team_id not in principal.team_roles:
            raise ValueError("授权主体不是目标团队成员")
        return cls(
            mode=AuthorizationMode.USER,
            principal=principal,
            scope=resolved_scope,
            request_id=request_id,
        )

    @classmethod
    def system(cls, actor: str) -> "AuthorizationContext":
        normalized = actor.strip()
        if not normalized:
            raise ValueError("系统授权必须声明 actor")
        return cls(mode=AuthorizationMode.SYSTEM, system_actor=normalized)

    @property
    def user_id(self) -> Optional[str]:
        return self.principal.user_id if self.principal else None

    @property
    def team_id(self) -> Optional[str]:
        return self.scope.team_id if self.scope else None

    @property
    def team_role(self) -> Optional[TeamRole]:
        if not self.principal or not self.team_id:
            return None
        return self.principal.team_roles.get(self.team_id)

    @property
    def is_admin(self) -> bool:
        return bool(self.principal and self.principal.is_admin)
