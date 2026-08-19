#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Immutable API-facing codebase version and build projections."""
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.knowledge_version import FrozenMapping
from app.domain.models.resource_governance import BuildState


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping(
            (key, _freeze_value(item))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


class CodebaseBuildProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    codebase_id: str
    version_id: str
    parent_version_id: Optional[str] = None
    command_key: str
    state: BuildState
    phase: Optional[str] = None
    progress: float = 0.0
    capabilities: tuple[Any, ...] = ()
    degraded_reasons: tuple[Any, ...] = ()
    metrics: FrozenMapping = Field(default_factory=FrozenMapping)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    last_event_seq: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    can_retry: bool = False
    can_cancel: bool = False

    @field_validator("capabilities", "degraded_reasons", mode="before")
    @classmethod
    def _freeze_sequences(cls, value: Any) -> tuple[Any, ...]:
        return tuple(_freeze_value(item) for item in (value or ()))

    @field_validator("metrics", mode="before")
    @classmethod
    def _freeze_metrics(cls, value: Any) -> FrozenMapping:
        return FrozenMapping(value or {})


class CodebaseVersionProjection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    codebase_id: str
    parent_version_id: Optional[str] = None
    build_id: Optional[str] = None
    state: CodebaseVersionState
    source_snapshot_key: Optional[str] = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: FrozenMapping = Field(default_factory=FrozenMapping)
    degraded_reasons: tuple[str, ...] = ()
    metrics: FrozenMapping = Field(default_factory=FrozenMapping)
    created_at: datetime
    published_at: Optional[datetime] = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: Optional[CodebaseBuildProjection] = None

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
    active_version_id: Optional[str] = None
    active_build: Optional[CodebaseBuildProjection] = None
    versions: tuple[CodebaseVersionProjection, ...] = ()

    @field_validator("versions", mode="before")
    @classmethod
    def _freeze_versions(
        cls,
        value: Any,
    ) -> tuple[CodebaseVersionProjection, ...]:
        return tuple(value or ())
