#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.service_api_key import ServiceApiKey
from .base import Base


class ServiceApiKeyORM(Base):
    __tablename__ = "service_api_keys"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, key: ServiceApiKey) -> "ServiceApiKeyORM":
        return cls(
            id=key.id,
            owner_user_id=key.owner_user_id,
            name=key.name,
            key_hash=key.key_hash,
            prefix=key.prefix,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )

    def update_from_domain(self, key: ServiceApiKey) -> None:
        self.name = key.name
        self.key_hash = key.key_hash
        self.prefix = key.prefix
        self.last_used_at = key.last_used_at
        self.revoked_at = key.revoked_at

    def to_domain(self) -> ServiceApiKey:
        return ServiceApiKey(
            id=self.id,
            owner_user_id=self.owner_user_id,
            name=self.name,
            key_hash=self.key_hash,
            prefix=self.prefix,
            last_used_at=self.last_used_at,
            revoked_at=self.revoked_at,
            created_at=self.created_at,
        )
