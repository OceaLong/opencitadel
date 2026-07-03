#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models.llm_model import LLMProvider


class LLMEndpointCreateRequest(BaseModel):
    display_name: str
    provider: LLMProvider = LLMProvider.OPENAI
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""


class LLMEndpointUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    provider: Optional[LLMProvider] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class LLMEndpointModelSummary(BaseModel):
    id: str
    display_name: str
    model_name: str
    is_default: bool = False


class LLMEndpointResponse(BaseModel):
    id: str
    display_name: str
    provider: LLMProvider
    base_url: str
    api_key: str = ""
    visibility: str = "global"
    owner_user_id: Optional[str] = None
    model_count: int = 0
    models: List[LLMEndpointModelSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LLMEndpointListResponse(BaseModel):
    endpoints: List[LLMEndpointResponse]
