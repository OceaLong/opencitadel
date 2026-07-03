#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import String, Float, Integer, Boolean, DateTime, Text, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities


class LLMModelORM(Base):
    """LLM模型ORM"""
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("llm_endpoints.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
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
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, model: LLMModel) -> "LLMModelORM":
        return cls(
            id=model.id,
            endpoint_id=model.endpoint_id,
            display_name=model.display_name,
            model_name=model.model_name,
            temperature=model.temperature,
            max_tokens=model.max_tokens,
            input_price_per_million=model.input_price_per_million,
            output_price_per_million=model.output_price_per_million,
            extra_params=model.extra_params,
            capabilities=model.capabilities.model_dump(),
            supports_multimodal=model.supports_multimodal,
            is_default=model.is_default,
            owner_user_id=model.owner_user_id,
            visibility=model.visibility.value,
        )

    def to_domain(
        self,
        *,
        provider: LLMProvider,
        base_url: str,
        api_key: str,
    ) -> LLMModel:
        raw_capabilities = self.capabilities or {}
        if raw_capabilities:
            capabilities = ModelCapabilities.model_validate(raw_capabilities)
        else:
            capabilities = ModelCapabilities.from_legacy_flag(self.supports_multimodal)
        return LLMModel(
            id=self.id,
            endpoint_id=self.endpoint_id,
            display_name=self.display_name,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model_name=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            extra_params=self.extra_params or {},
            capabilities=capabilities,
            supports_multimodal=self.supports_multimodal,
            is_default=self.is_default,
            owner_user_id=self.owner_user_id,
            visibility=self.visibility,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
