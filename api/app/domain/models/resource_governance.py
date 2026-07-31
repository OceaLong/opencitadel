#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared resource build, version, and immutable session binding values."""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResourceKind(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    CODEBASE = "codebase"


class BuildState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_BUILD_PHASE_ORDER: dict[ResourceKind, tuple[str, ...]] = {
    ResourceKind.KNOWLEDGE_BASE: (
        "parse",
        "chunk",
        "keyword_index",
        "vector_index",
        "graph",
        "validate",
        "publish",
    ),
    ResourceKind.CODEBASE: (
        "materialize",
        "snapshot",
        "analyze",
        "keyword_index",
        "vector_index",
        "artifacts",
        "validate",
        "publish",
    ),
}


def build_phase_rank(
    resource_kind: ResourceKind,
    phase: Optional[str],
) -> Optional[int]:
    """Return the durable rank for a recognized build phase.

    Unknown phases remain forward-compatible and are intentionally unranked.
    A resource-specific runner must use its published phase vocabulary when it
    wants the shared monotonicity invariant.
    """
    if phase is None:
        return None
    try:
        normalized_kind = ResourceKind(resource_kind)
    except ValueError:
        return None
    try:
        return _BUILD_PHASE_ORDER[normalized_kind].index(phase)
    except (KeyError, ValueError):
        return None


def build_phase_regresses(
    resource_kind: ResourceKind,
    current_phase: Optional[str],
    candidate_phase: Optional[str],
) -> bool:
    """Whether a recognized durable phase moves backwards."""
    current_rank = build_phase_rank(resource_kind, current_phase)
    candidate_rank = build_phase_rank(resource_kind, candidate_phase)
    return (
        current_rank is not None
        and candidate_rank is not None
        and candidate_rank < current_rank
    )


class ResourceBuild(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    resource_kind: ResourceKind
    resource_id: str
    version_id: str
    parent_version_id: Optional[str] = None
    command_key: str
    state: BuildState = BuildState.QUEUED
    phase: Optional[str] = None
    progress: float = 0.0
    capabilities: list[Any] = Field(default_factory=list)
    degraded_reasons: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    last_event_seq: int = 0
    created_by: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ResourceBuildEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    build_id: str
    seq: int
    phase: Optional[str] = None
    state: BuildState
    progress: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ResourceBindingProjection(BaseModel):
    """Immutable, non-secret event metadata for one pinned resource."""

    model_config = ConfigDict(frozen=True)

    binding_id: str
    resource_kind: ResourceKind
    resource_id: str
    version_id: str


class SessionResourceBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    resource_kind: ResourceKind
    resource_id: str
    version_id: str
    is_current: bool = True
    supersedes_binding_id: Optional[str] = None
    bound_by: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_projection(self) -> ResourceBindingProjection:
        return ResourceBindingProjection(
            binding_id=self.id,
            resource_kind=self.resource_kind,
            resource_id=self.resource_id,
            version_id=self.version_id,
        )


class PublishedResourceVersion(BaseModel):
    """Provider result after scope and publication validation.

    The shared binding service still verifies every field because providers
    are independently implemented extension points.
    """

    model_config = ConfigDict(frozen=True)

    resource_kind: ResourceKind
    resource_id: str
    version_id: str
    state: BuildState = BuildState.SUCCEEDED
    published: bool = True
    degraded: bool = False
    capabilities: dict[str, bool] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)

    def __init__(self, *args: object, **data: Any) -> None:
        """Accept the compact provider protocol constructor as well as kwargs."""
        if len(args) > 3:
            raise TypeError(
                "PublishedResourceVersion accepts at most three positional "
                "arguments"
            )
        names = ("resource_kind", "resource_id", "version_id")
        if "kind" in data:
            if "resource_kind" in data:
                raise TypeError("kind and resource_kind are mutually exclusive")
            data["resource_kind"] = data.pop("kind")
        for name, value in zip(names, args):
            if name in data:
                raise TypeError(f"{name} was provided more than once")
            data[name] = value
        super().__init__(**data)

    @model_validator(mode="before")
    @classmethod
    def _normalize_protocol_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        kind_aliases = {
            "kb": ResourceKind.KNOWLEDGE_BASE,
            "cb": ResourceKind.CODEBASE,
        }
        normalized["resource_kind"] = kind_aliases.get(
            normalized.get("resource_kind"),
            normalized.get("resource_kind"),
        )

        has_degraded = "degraded" in normalized
        has_state = "state" in normalized
        if has_degraded and not has_state and normalized["degraded"]:
            normalized["state"] = BuildState.DEGRADED
        elif has_state and not has_degraded:
            normalized["degraded"] = (
                normalized["state"] == BuildState.DEGRADED
                or normalized["state"] == BuildState.DEGRADED.value
            )
        elif has_state and has_degraded:
            state_is_degraded = (
                normalized["state"] == BuildState.DEGRADED
                or normalized["state"] == BuildState.DEGRADED.value
            )
            if bool(normalized["degraded"]) != state_is_degraded:
                raise ValueError("degraded must agree with state")
        return normalized

    @property
    def kind(self) -> ResourceKind:
        return self.resource_kind
