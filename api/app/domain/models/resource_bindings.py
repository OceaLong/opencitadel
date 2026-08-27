"""Resource identities, candidate intents, and immutable session bindings."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS


class ResourceKind(StrEnum):
    KNOWLEDGE_BASE = "knowledge_base"
    CODEBASE = "codebase"


class PublicationState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


class ResourceBuildIntent(BaseModel):
    """Immutable admission input for one candidate version.

    This value is deliberately not a lifecycle aggregate.  The candidate
    version owns artifact state; the execution Run owns scheduling, progress,
    cancellation, retry and terminal state.
    """

    model_config = ConfigDict(frozen=True)

    build_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    resource_kind: ResourceKind
    resource_id: str
    version_id: str
    parent_version_id: str | None = None
    embedding_model_id: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_embedding_snapshot(self) -> "ResourceBuildIntent":
        values = (self.embedding_model_id, self.embedding_dimensions)
        if (values[0] is None) != (values[1] is None):
            raise ValueError("embedding model and dimensions must be frozen together")
        if self.embedding_dimensions not in {None, PLATFORM_EMBEDDING_DIMENSIONS}:
            raise ValueError(f"embedding dimensions must be {PLATFORM_EMBEDDING_DIMENSIONS}")
        return self


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
    supersedes_binding_id: str | None = None
    bound_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

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
    state: PublicationState = PublicationState.READY
    published: bool = True
    degraded: bool = False
    capabilities: dict[str, bool] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)

    def __init__(self, *args: object, **data: Any) -> None:
        """Accept the compact provider protocol constructor as well as kwargs."""
        if len(args) > 3:
            raise TypeError("PublishedResourceVersion accepts at most three positional arguments")
        names = ("resource_kind", "resource_id", "version_id")
        if "kind" in data:
            if "resource_kind" in data:
                raise TypeError("kind and resource_kind are mutually exclusive")
            data["resource_kind"] = data.pop("kind")
        for name, value in zip(names, args, strict=False):
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
            normalized["state"] = PublicationState.DEGRADED
        elif has_state and not has_degraded:
            normalized["degraded"] = (
                normalized["state"] == PublicationState.DEGRADED
                or normalized["state"] == PublicationState.DEGRADED.value
            )
        elif has_state and has_degraded:
            state_is_degraded = (
                normalized["state"] == PublicationState.DEGRADED
                or normalized["state"] == PublicationState.DEGRADED.value
            )
            if bool(normalized["degraded"]) != state_is_degraded:
                raise ValueError("degraded must agree with state")
        return normalized

    @property
    def kind(self) -> ResourceKind:
        return self.resource_kind
