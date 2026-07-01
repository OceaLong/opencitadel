#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.user_quota import UserQuota
from .base import Base


class UserQuotaORM(Base):
    __tablename__ = "user_quotas"

    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    monthly_token_limit: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    daily_session_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_concurrent_tasks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, quota: UserQuota) -> "UserQuotaORM":
        return cls(
            user_id=quota.user_id,
            monthly_token_limit=quota.monthly_token_limit,
            daily_session_limit=quota.daily_session_limit,
            max_concurrent_tasks=quota.max_concurrent_tasks,
            max_storage_bytes=quota.max_storage_bytes,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
        )

    def update_from_domain(self, quota: UserQuota) -> None:
        self.monthly_token_limit = quota.monthly_token_limit
        self.daily_session_limit = quota.daily_session_limit
        self.max_concurrent_tasks = quota.max_concurrent_tasks
        self.max_storage_bytes = quota.max_storage_bytes
        self.updated_at = quota.updated_at

    def to_domain(self) -> UserQuota:
        return UserQuota(
            user_id=self.user_id,
            monthly_token_limit=self.monthly_token_limit,
            daily_session_limit=self.daily_session_limit,
            max_concurrent_tasks=self.max_concurrent_tasks,
            max_storage_bytes=self.max_storage_bytes,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
