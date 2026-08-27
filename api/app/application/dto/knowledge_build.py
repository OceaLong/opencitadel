"""Immutable API-facing knowledge version and build projections."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.execution.run import RunStatus
from app.domain.models.knowledge_version import FrozenMapping, KnowledgeVersionState


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping((key, _freeze_value(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


class KnowledgeBuildProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    run_id: str | None = None
    knowledge_base_id: str
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


class KnowledgeVersionProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    knowledge_base_id: str
    parent_version_id: str | None = None
    build_id: str | None = None
    state: KnowledgeVersionState
    capabilities: FrozenMapping = Field(default_factory=FrozenMapping)
    degraded_reasons: tuple[str, ...] = ()
    metrics: FrozenMapping = Field(default_factory=FrozenMapping)
    created_at: datetime
    published_at: datetime | None = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: KnowledgeBuildProjection | None = None

    @field_validator("capabilities", "metrics", mode="before")
    @classmethod
    def _freeze_mappings(cls, value: Any) -> FrozenMapping:
        return FrozenMapping(value or {})

    @field_validator("degraded_reasons", mode="before")
    @classmethod
    def _freeze_reasons(cls, value: Any) -> tuple[str, ...]:
        return tuple(value or ())


class KnowledgeVersionHistoryProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    knowledge_base_id: str
    active_version_id: str | None = None
    active_build: KnowledgeBuildProjection | None = None
    versions: tuple[KnowledgeVersionProjection, ...] = ()

    @field_validator("versions", mode="before")
    @classmethod
    def _freeze_versions(
        cls,
        value: Any,
    ) -> tuple[KnowledgeVersionProjection, ...]:
        return tuple(value or ())
