#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import String, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AppConfigModel(Base):
    """应用运行时配置 ORM（global 行 + 可选 user 覆盖行）"""

    __tablename__ = "app_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )


class AppConfigRevisionModel(Base):
    __tablename__ = "app_config_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
