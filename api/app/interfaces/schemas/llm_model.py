#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.llm_model import ModelCapabilities


class LLMModelCreateRequest(BaseModel):
    endpoint_id: str
    display_name: str
    model_name: str = "gpt-4o"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=8192, ge=1)
    input_price_per_million: float = Field(default=0.0, ge=0)
    output_price_per_million: float = Field(default=0.0, ge=0)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    supports_multimodal: bool = False
    is_default: bool = False


class LLMModelUpdateRequest(BaseModel):
    endpoint_id: Optional[str] = None
    display_name: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    input_price_per_million: Optional[float] = Field(default=None, ge=0)
    output_price_per_million: Optional[float] = Field(default=None, ge=0)
    extra_params: Optional[Dict[str, Any]] = None
    capabilities: Optional[ModelCapabilities] = None
    supports_multimodal: Optional[bool] = None
    is_default: Optional[bool] = None


class LLMModelResponse(BaseModel):
    id: str
    endpoint_id: str
    display_name: str
    provider: str
    base_url: str
    api_key: str = ""
    model_name: str
    temperature: float
    max_tokens: int
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    supports_multimodal: bool = False
    is_default: bool
    visibility: str = "global"
    owner_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LLMModelListResponse(BaseModel):
    models: List[LLMModelResponse]


class MultimodalProbeResponse(BaseModel):
    status: str
    message: str = ""
    error_code: Optional[str] = None
