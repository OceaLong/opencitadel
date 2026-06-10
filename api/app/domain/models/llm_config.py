#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Legacy LLM config DTO for deprecated /app-config/llm API compatibility."""
from pydantic import BaseModel, HttpUrl, Field


class LLMConfig(BaseModel):
    """LLM 提供商配置（仅用于 API 兼容，运行时模型见 llm_model 表）"""
    base_url: HttpUrl = "https://api.deepseek.com"
    api_key: str = ""
    model_name: str = "deepseek-chat"
    temperature: float = Field(0.7)
    max_tokens: int = Field(8192, ge=0)
