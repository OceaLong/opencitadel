#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import String, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - dev without pgvector package
    Vector = None  # type: ignore[misc, assignment]


class AppConfigModel(Base):
    """应用运行时配置 ORM（单行 default 记录）"""

    __tablename__ = "app_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
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
