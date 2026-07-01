#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, model_validator

_DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class LLMProvider(str, Enum):
    """LLM提供商枚举"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    AZURE = "azure"


class ModelCapabilities(BaseModel):
    """模型多模态与图像相关能力描述。"""
    vision: bool = False
    vision_with_tools: bool = True
    audio: bool = False
    video: bool = False
    image_generation: bool = False
    max_image_bytes: int = Field(default=_DEFAULT_MAX_IMAGE_BYTES, ge=1)
    max_images_per_request: int = Field(default=8, ge=1)
    max_video_frames: int = Field(default=8, ge=1)
    image_encoding: Literal["data_url", "url"] = "data_url"
    structured_output: Literal["auto", "json_schema", "json_object", "none"] = "auto"

    @classmethod
    def from_legacy_flag(cls, supports_multimodal: bool) -> "ModelCapabilities":
        return cls(vision=supports_multimodal)


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
    input_price_per_million: float = Field(default=0.0, ge=0)
    output_price_per_million: float = Field(default=0.0, ge=0)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    supports_multimodal: bool = False
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _sync_capabilities_and_legacy_flag(self) -> "LLMModel":
        if self.supports_multimodal and not self.capabilities.vision:
            self.capabilities = self.capabilities.model_copy(update={"vision": True})
        elif self.capabilities.vision:
            self.supports_multimodal = True
        return self

    def mask_api_key(self) -> "LLMModel":
        """返回api_key已脱敏的副本"""
        masked = self.model_copy(deep=True)
        if masked.api_key:
            masked.api_key = masked.api_key[:4] + "****" + masked.api_key[-4:] if len(masked.api_key) > 8 else "****"
        return masked

