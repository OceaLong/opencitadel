"""Immutable API-facing codebase version and build projections."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.execution.run import RunStatus
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.knowledge_version import FrozenMapping


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping((key, _freeze_value(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


class CodebaseBuildProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    run_id: str | None = None
    codebase_id: str
    version_id: str
    status: RunStatus
    phase: str | None = None
    progress: int = 0
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    can_retry: bool = False
    can_cancel: bool = False


class CodebaseVersionProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    codebase_id: str
    parent_version_id: str | None = None
    build_id: str | None = None
    state: CodebaseVersionState
    source_snapshot_key: str | None = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: FrozenMapping = Field(default_factory=FrozenMapping)
    degraded_reasons: tuple[str, ...] = ()
    metrics: FrozenMapping = Field(default_factory=FrozenMapping)
    created_at: datetime
    published_at: datetime | None = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: CodebaseBuildProjection | None = None

    @field_validator("capabilities", "metrics", mode="before")
    @classmethod
    def _freeze_mappings(cls, value: Any) -> FrozenMapping:
        return FrozenMapping(value or {})

    @field_validator("degraded_reasons", mode="before")
    @classmethod
    def _freeze_reasons(cls, value: Any) -> tuple[str, ...]:
        return tuple(value or ())


class CodebaseVersionHistoryProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    codebase_id: str
    active_version_id: str | None = None
    active_build: CodebaseBuildProjection | None = None
    versions: tuple[CodebaseVersionProjection, ...] = ()

    @field_validator("versions", mode="before")
    @classmethod
    def _freeze_versions(
        cls,
        value: Any,
    ) -> tuple[CodebaseVersionProjection, ...]:
        return tuple(value or ())
