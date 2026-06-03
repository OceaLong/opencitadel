#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LLMTokenUsage(BaseModel):
    """单次 LLM 调用的 token 使用记录。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    agent: str = ""
    step: str = ""
    model_id: Optional[str] = None
    model_name: str = ""
    call_type: str = "stream"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class SessionTokenUsageSummary(BaseModel):
    """会话级 token 汇总。"""
    session_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    call_count: int = 0
