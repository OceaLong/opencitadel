#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.refresh_token import RefreshToken
from .base import Base


class RefreshTokenORM(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, token: RefreshToken) -> "RefreshTokenORM":
        return cls(
            id=token.id,
            user_id=token.user_id,
            token_hash=token.token_hash,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
            user_agent=token.user_agent,
            ip_address=token.ip_address,
            created_at=token.created_at,
        )

    def update_from_domain(self, token: RefreshToken) -> None:
        self.token_hash = token.token_hash
        self.expires_at = token.expires_at
        self.revoked_at = token.revoked_at
        self.user_agent = token.user_agent
        self.ip_address = token.ip_address

    def to_domain(self) -> RefreshToken:
        return RefreshToken(
            id=self.id,
            user_id=self.user_id,
            token_hash=self.token_hash,
            expires_at=self.expires_at,
            revoked_at=self.revoked_at,
            user_agent=self.user_agent,
            ip_address=self.ip_address,
            created_at=self.created_at,
        )
