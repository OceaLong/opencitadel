#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models.skill import SkillAgentParams


class SkillCreateRequest(BaseModel):
    name: str
    slug: str = ""
    description: str = ""
    icon: str = "🤖"
    category: str = "general"
    system_prompt: str = ""
    allowed_tools: List[str] = Field(default_factory=list)
    recommended_model_id: Optional[str] = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: List[str] = Field(default_factory=list)
    enabled: bool = True


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    system_prompt: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    recommended_model_id: Optional[str] = None
    agent_params: Optional[SkillAgentParams] = None
    examples: Optional[List[str]] = None
    enabled: Optional[bool] = None


class SkillResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    icon: str
    category: str
    system_prompt: str
    allowed_tools: List[str]
    recommended_model_id: Optional[str]
    agent_params: SkillAgentParams
    examples: List[str]
    is_builtin: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillListResponse(BaseModel):
    skills: List[SkillResponse]


class SkillSummaryResponse(BaseModel):
    id: str
    name: str
    icon: str
    examples: List[str] = Field(default_factory=list)
