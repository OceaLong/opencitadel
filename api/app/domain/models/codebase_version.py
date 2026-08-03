#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Immutable codebase analysis version domain values."""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CodebaseVersionState(str, Enum):
    BUILDING = "building"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class CodeEvidenceRef(BaseModel):
    """Exact source evidence for one code analysis fact."""

    model_config = ConfigDict(frozen=True)

    version_id: str
    file_id: str
    path: str
    start_line: int = 0
    end_line: int = 0
    symbol_id: Optional[str] = None
    analyzer: str = ""
    confidence: float = 0.0


class CodebaseVersion(BaseModel):
    """A candidate or published immutable snapshot of one logical codebase."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    parent_version_id: Optional[str] = None
    build_id: Optional[str] = None
    state: CodebaseVersionState = CodebaseVersionState.BUILDING
    source_snapshot_key: Optional[str] = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: dict[str, bool] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    legacy_snapshot: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    published_at: Optional[datetime] = None

    @property
    def published(self) -> bool:
        return (
            self.published_at is not None
            and self.state
            in {
                CodebaseVersionState.READY,
                CodebaseVersionState.DEGRADED,
            }
        )

    @property
    def degraded(self) -> bool:
        return self.state is CodebaseVersionState.DEGRADED
