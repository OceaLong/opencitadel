#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from sqlalchemy import String, DateTime, Text, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMProvider, ResourceVisibility
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class LLMEndpointORM(Base):
    """Shared LLM endpoint ORM."""

    __tablename__ = "llm_endpoints"

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
    def from_domain(
        cls,
        endpoint: LLMEndpoint,
        encrypted_api_key: str,
        *,
        api_key_encryption: str = ApiKeyEncryption.LEGACY_PLAINTEXT,
    ) -> "LLMEndpointORM":
        return cls(
            id=endpoint.id,
            display_name=endpoint.display_name,
            provider=endpoint.provider.value,
            base_url=endpoint.base_url,
            api_key=encrypted_api_key,
            api_key_encryption=api_key_encryption,
            owner_user_id=endpoint.owner_user_id,
            visibility=endpoint.visibility.value,
        )

    def to_domain(self, decrypted_api_key: str) -> LLMEndpoint:
        return LLMEndpoint(
            id=self.id,
            display_name=self.display_name,
            provider=LLMProvider(self.provider),
            base_url=self.base_url,
            api_key=decrypted_api_key,
            owner_user_id=self.owner_user_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
