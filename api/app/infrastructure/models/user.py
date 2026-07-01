#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.user import GlobalRole, User, UserStatus
from .base import Base


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    avatar_url: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    global_role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'user'"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @classmethod
    def from_domain(cls, user: User) -> "UserORM":
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            password_hash=user.password_hash,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            global_role=user.global_role.value,
            status=user.status.value,
            token_version=user.token_version,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login_at=user.last_login_at,
        )

    def update_from_domain(self, user: User) -> None:
        self.email = user.email
        self.username = user.username
        self.password_hash = user.password_hash
        self.display_name = user.display_name
        self.avatar_url = user.avatar_url
        self.global_role = user.global_role.value
        self.status = user.status.value
        self.token_version = user.token_version
        self.updated_at = user.updated_at
        self.last_login_at = user.last_login_at

    def to_domain(self) -> User:
        return User(
            id=self.id,
            email=self.email,
            username=self.username,
            password_hash=self.password_hash,
            display_name=self.display_name,
            avatar_url=self.avatar_url,
            global_role=GlobalRole(self.global_role),
            status=UserStatus(self.status),
            token_version=self.token_version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            last_login_at=self.last_login_at,
        )
