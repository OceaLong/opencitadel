"""Retention identities are never an authorization grant."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResourceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource_kind: Literal["knowledge_base", "artifact", "file", "execution_content"]
    resource_id: str = Field(min_length=1, max_length=255)
    resource_version: str = Field(min_length=1, max_length=255)


class PinValidation(BaseModel):
    resource: ResourceIdentity
    available: bool
    reason: str | None = None


class ResourcePinned(ValueError):
    """Ordinary physical deletion would break a live retention owner."""

    def __init__(self, message, *, resource_kind=None, resource_id=None):
        super().__init__(message)
        self.resource_kind = resource_kind
        self.resource_id = resource_id


class ResourceUnavailable(ValueError):
    """The exact immutable resource or its present authority is unavailable."""
