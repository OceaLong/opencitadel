#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models.llm_model import LLMProvider, ResourceVisibility
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


class LLMEndpoint(BaseModel):
    """Shared LLM connection configuration (provider, URL, API key)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    display_name: str = ""
    provider: LLMProvider = LLMProvider.OPENAI
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    owner_user_id: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def mask_api_key(self) -> "LLMEndpoint":
        masked = self.model_copy(deep=True)
        if masked.api_key:
            masked.api_key = ApiKeyCipher.mask(masked.api_key)
        return masked
