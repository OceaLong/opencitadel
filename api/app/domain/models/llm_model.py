#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LLMProvider(str, Enum):
    """LLM提供商枚举"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    AZURE = "azure"


class LLMModel(BaseModel):
    """LLM模型配置领域模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    display_name: str = ""
    provider: LLMProvider = LLMProvider.OPENAI
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model_name: str = "gpt-4o"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=8192, ge=1)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    supports_multimodal: bool = False
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def mask_api_key(self) -> "LLMModel":
        """返回api_key已脱敏的副本"""
        masked = self.model_copy(deep=True)
        if masked.api_key:
            masked.api_key = masked.api_key[:4] + "****" + masked.api_key[-4:] if len(masked.api_key) > 8 else "****"
        return masked

    @classmethod
    def from_llm_config(cls, llm_config, display_name: str = "默认模型") -> "LLMModel":
        """从旧版LLMConfig迁移创建"""
        return cls(
            display_name=display_name,
            provider=LLMProvider.OPENAI,
            base_url=str(llm_config.base_url),
            api_key=llm_config.api_key,
            model_name=llm_config.model_name,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
            is_default=True,
        )
