#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ResourceVisibility(str, Enum):
    GLOBAL = "global"
    PRIVATE = "private"


class SkillResource(BaseModel):
    name: str = ""
    kind: Literal["template", "script", "reference"] = "reference"
    path: Optional[str] = None
    content: Optional[str] = None


class SkillAgentParams(BaseModel):
    """Skill覆盖的Agent参数"""
    max_iterations: Optional[int] = None
    max_retries: Optional[int] = None
    max_search_results: Optional[int] = None
    temperature_override: Optional[float] = None
    tool_gate_call_level_enabled: Optional[bool] = None
    writing_style_override: Optional[Literal["prose", "adaptive"]] = None


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
    resources: List[SkillResource] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    mcp_server_refs: List[str] = Field(default_factory=list)
    a2a_server_refs: List[str] = Field(default_factory=list)
    recommended_model_id: Optional[str] = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: List[str] = Field(default_factory=list)
    override_base_rules: bool = False
    auto_recommend: bool = True
    source_format: Literal["native", "claude_md"] = "native"
    is_builtin: bool = False
    enabled: bool = True
    owner_user_id: Optional[str] = None
    visibility: str = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SkillSummary(BaseModel):
    """Skill摘要，用于会话详情返回"""
    id: str
    name: str
    icon: str = "🤖"
    examples: List[str] = Field(default_factory=list)


class SkillRecommendResult(BaseModel):
    skill_id: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
