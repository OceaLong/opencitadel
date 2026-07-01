#!/usr/bin/env python
# -*- coding: utf-8 -*-
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field

from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole


class OwnerScopeType(str, Enum):
    PERSONAL = "personal"
    TEAM = "team"


class OwnerScope(BaseModel):
    type: OwnerScopeType = OwnerScopeType.PERSONAL
    user_id: str
    team_id: Optional[str] = None

    @classmethod
    def personal(cls, user_id: str) -> "OwnerScope":
        return cls(type=OwnerScopeType.PERSONAL, user_id=user_id)

    @classmethod
    def team(cls, user_id: str, team_id: str) -> "OwnerScope":
        return cls(type=OwnerScopeType.TEAM, user_id=user_id, team_id=team_id)


class Principal(BaseModel):
    user_id: str
    global_role: GlobalRole = GlobalRole.USER
    token_version: int = 0
    team_roles: Dict[str, TeamRole] = Field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.global_role == GlobalRole.ADMIN


class WorkspaceContext(BaseModel):
    principal: Principal
    scope: OwnerScope
