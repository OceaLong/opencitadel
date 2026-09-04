from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models.skill import SkillAgentParams, SkillResource


class SkillCreateRequest(BaseModel):
    name: str
    slug: str = ""
    description: str = ""
    icon: str = "🤖"
    category: str = "general"
    system_prompt: str = ""
    body: str = ""
    resources: list[SkillResource] = Field(default_factory=list)
    # None=不限制 / []=禁全部（D11）
    allowed_tools: list[str] | None = None
    mcp_server_refs: list[str] = Field(default_factory=list)
    a2a_server_refs: list[str] = Field(default_factory=list)
    recommended_model_id: str | None = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: list[str] = Field(default_factory=list)
    override_base_rules: bool = False
    source_format: str = "native"
    enabled: bool = True


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    system_prompt: str | None = None
    body: str | None = None
    resources: list[SkillResource] | None = None
    allowed_tools: list[str] | None = None
    mcp_server_refs: list[str] | None = None
    a2a_server_refs: list[str] | None = None
    recommended_model_id: str | None = None
    agent_params: SkillAgentParams | None = None
    examples: list[str] | None = None
    override_base_rules: bool | None = None
    source_format: str | None = None
    enabled: bool | None = None


class SkillResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    icon: str
    category: str
    system_prompt: str
    body: str = ""
    resources: list[SkillResource] = Field(default_factory=list)
    # None=不限制 / []=禁全部（D11）；UI 据此提示"未限制工具"。
    allowed_tools: list[str] | None = None
    mcp_server_refs: list[str] = Field(default_factory=list)
    a2a_server_refs: list[str] = Field(default_factory=list)
    recommended_model_id: str | None
    agent_params: SkillAgentParams
    examples: list[str]
    override_base_rules: bool = False
    source_format: str = "native"
    is_builtin: bool
    enabled: bool
    visibility: str = "global"
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]


class SkillSummaryResponse(BaseModel):
    id: str
    name: str
    icon: str
    examples: list[str] = Field(default_factory=list)


class SkillImportRequest(BaseModel):
    content: str
    slug: str = ""
