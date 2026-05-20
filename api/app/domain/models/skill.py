#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SkillAgentParams(BaseModel):
    """Skill覆盖的Agent参数"""
    max_iterations: Optional[int] = None
    max_retries: Optional[int] = None
    max_search_results: Optional[int] = None
    temperature_override: Optional[float] = None


class Skill(BaseModel):
    """Skill技能模板领域模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    slug: str = ""
    description: str = ""
    icon: str = "🤖"
    category: str = "general"
    system_prompt: str = ""
    allowed_tools: List[str] = Field(default_factory=list)
    recommended_model_id: Optional[str] = None
    agent_params: SkillAgentParams = Field(default_factory=SkillAgentParams)
    examples: List[str] = Field(default_factory=list)
    is_builtin: bool = False
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SkillSummary(BaseModel):
    """Skill摘要，用于会话详情返回"""
    id: str
    name: str
    icon: str = "🤖"
    examples: List[str] = Field(default_factory=list)
