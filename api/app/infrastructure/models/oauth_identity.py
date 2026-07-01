#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.oauth_identity import OAuthIdentity
from .base import Base


class OAuthIdentityORM(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, identity: OAuthIdentity) -> "OAuthIdentityORM":
        return cls(
            id=identity.id,
            user_id=identity.user_id,
            provider=identity.provider,
            provider_user_id=identity.provider_user_id,
            email=identity.email,
            email_verified=identity.email_verified,
            created_at=identity.created_at,
            updated_at=identity.updated_at,
        )

    def update_from_domain(self, identity: OAuthIdentity) -> None:
        self.user_id = identity.user_id
        self.provider = identity.provider
        self.provider_user_id = identity.provider_user_id
        self.email = identity.email
        self.email_verified = identity.email_verified
        self.updated_at = identity.updated_at

    def to_domain(self) -> OAuthIdentity:
        return OAuthIdentity(
            id=self.id,
            user_id=self.user_id,
            provider=self.provider,
            provider_user_id=self.provider_user_id,
            email=self.email,
            email_verified=self.email_verified,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
