from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole


class OwnerScopeType(StrEnum):
    PERSONAL = "personal"
    TEAM = "team"


class OwnerScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: OwnerScopeType = OwnerScopeType.PERSONAL
    user_id: str
    team_id: str | None = None

    @classmethod
    def personal(cls, user_id: str) -> OwnerScope:
        return cls(type=OwnerScopeType.PERSONAL, user_id=user_id)

    @classmethod
    def team(cls, user_id: str, team_id: str) -> OwnerScope:
        return cls(type=OwnerScopeType.TEAM, user_id=user_id, team_id=team_id)


class Principal(BaseModel):
    user_id: str
    global_role: GlobalRole = GlobalRole.USER
    token_version: int = 0
    team_roles: dict[str, TeamRole] = Field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.global_role == GlobalRole.ADMIN

    @property
    def is_auditor(self) -> bool:
        return self.global_role == GlobalRole.AUDITOR


class WorkspaceContext(BaseModel):
    principal: Principal
    scope: OwnerScope
