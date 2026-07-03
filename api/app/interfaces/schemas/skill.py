#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

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
    resources: List[SkillResource] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    mcp_server_refs: List[str] = Field(default_factory=list)
    a2a_server_refs: List[str] = Field(default_factory=list)
    recommended_model_id: Optional[str] = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: List[str] = Field(default_factory=list)
    override_base_rules: bool = False
    auto_recommend: bool = True
    source_format: str = "native"
    enabled: bool = True


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    system_prompt: Optional[str] = None
    body: Optional[str] = None
    resources: Optional[List[SkillResource]] = None
    allowed_tools: Optional[List[str]] = None
    mcp_server_refs: Optional[List[str]] = None
    a2a_server_refs: Optional[List[str]] = None
    recommended_model_id: Optional[str] = None
    agent_params: Optional[SkillAgentParams] = None
    examples: Optional[List[str]] = None
    override_base_rules: Optional[bool] = None
    auto_recommend: Optional[bool] = None
    source_format: Optional[str] = None
    enabled: Optional[bool] = None


class SkillResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    icon: str
    category: str
    system_prompt: str
    body: str = ""
    resources: List[SkillResource] = Field(default_factory=list)
    allowed_tools: List[str]
    mcp_server_refs: List[str] = Field(default_factory=list)
    a2a_server_refs: List[str] = Field(default_factory=list)
    recommended_model_id: Optional[str]
    agent_params: SkillAgentParams
    examples: List[str]
    override_base_rules: bool = False
    auto_recommend: bool = True
    source_format: str = "native"
    is_builtin: bool
    enabled: bool
    visibility: str = "global"
    owner_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SkillListResponse(BaseModel):
    skills: List[SkillResponse]


class SkillSummaryResponse(BaseModel):
    id: str
    name: str
    icon: str
    examples: List[str] = Field(default_factory=list)


class SkillRecommendResponse(BaseModel):
    skill_id: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class SkillImportRequest(BaseModel):
    content: str
    slug: str = ""
