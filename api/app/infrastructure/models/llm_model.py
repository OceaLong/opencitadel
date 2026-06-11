#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import String, Float, Integer, Boolean, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class LLMModelORM(Base):
    """LLM模型ORM"""
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    provider: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'openai'"))
    base_url: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    api_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    api_key_encryption: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'legacy_plaintext'"),
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    temperature: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.7"))
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8192"))
    input_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    output_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    extra_params: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    capabilities: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    supports_multimodal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(
        cls,
        model: LLMModel,
        encrypted_api_key: str,
        *,
        api_key_encryption: str = ApiKeyEncryption.LEGACY_PLAINTEXT,
    ) -> "LLMModelORM":
        return cls(
            id=model.id,
            display_name=model.display_name,
            provider=model.provider.value,
            base_url=model.base_url,
            api_key=encrypted_api_key,
            api_key_encryption=api_key_encryption,
            model_name=model.model_name,
            temperature=model.temperature,
            max_tokens=model.max_tokens,
            input_price_per_million=model.input_price_per_million,
            output_price_per_million=model.output_price_per_million,
            extra_params=model.extra_params,
            capabilities=model.capabilities.model_dump(),
            supports_multimodal=model.supports_multimodal,
            is_default=model.is_default,
        )

    def to_domain(self, decrypted_api_key: str) -> LLMModel:
        raw_capabilities = self.capabilities or {}
        if raw_capabilities:
            capabilities = ModelCapabilities.model_validate(raw_capabilities)
        else:
            capabilities = ModelCapabilities.from_legacy_flag(self.supports_multimodal)
        return LLMModel(
            id=self.id,
            display_name=self.display_name,
            provider=LLMProvider(self.provider),
            base_url=self.base_url,
            api_key=decrypted_api_key,
            model_name=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            extra_params=self.extra_params or {},
            capabilities=capabilities,
            supports_multimodal=self.supports_multimodal,
            is_default=self.is_default,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
