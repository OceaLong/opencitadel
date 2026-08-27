import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class ResourceVisibility(StrEnum):
    GLOBAL = "global"
    PRIVATE = "private"


class SkillResource(BaseModel):
    name: str = ""
    kind: Literal["template", "script", "reference"] = "reference"
    path: str | None = None
    content: str | None = None


class SkillAgentParams(BaseModel):
    """Execution settings applied when admitting an Agent Run."""

    max_iterations: int | None = Field(default=None, ge=1, le=100)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    temperature_override: float | None = Field(default=None, ge=0, le=2)


class Skill(BaseModel):
    """Skill技能模板领域模型"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    slug: str = ""
    description: str = ""
    icon: str = "🤖"
    category: str = "general"
    system_prompt: str = ""
    body: str = ""
    resources: list[SkillResource] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    mcp_server_refs: list[str] = Field(default_factory=list)
    a2a_server_refs: list[str] = Field(default_factory=list)
    recommended_model_id: str | None = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: list[str] = Field(default_factory=list)
    override_base_rules: bool = False
    source_format: Literal["native", "claude_md"] = "native"
    is_builtin: bool = False
    enabled: bool = True
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: str = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillSummary(BaseModel):
    """Skill摘要，用于会话详情返回"""

    id: str
    name: str
    icon: str = "🤖"
    examples: list[str] = Field(default_factory=list)
