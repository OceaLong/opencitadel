"""Immutable codebase analysis version domain values."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CodebaseVersionState(StrEnum):
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
    symbol_id: str | None = None
    analyzer: str = ""
    confidence: float = 0.0


class CodebaseVersion(BaseModel):
    """A candidate or published immutable snapshot of one logical codebase."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    parent_version_id: str | None = None
    build_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_key: str = Field(default_factory=lambda: uuid.uuid4().hex * 2)
    state: CodebaseVersionState = CodebaseVersionState.BUILDING
    source_snapshot_key: str | None = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: dict[str, bool] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None

    @property
    def published(self) -> bool:
        return self.published_at is not None and self.state in {
            CodebaseVersionState.READY,
            CodebaseVersionState.DEGRADED,
        }

    @property
    def degraded(self) -> bool:
        return self.state is CodebaseVersionState.DEGRADED
