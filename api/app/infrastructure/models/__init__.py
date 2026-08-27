"""Shared SQLAlchemy metadata without importing every persistence adapter."""

from app.infrastructure.models.base import Base

__all__ = ["Base"]
