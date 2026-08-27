"""Revision, active-head, and consistent-pair metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.execution.commands import normalize_utc, require_non_empty
from app.domain.runtime_policy.execution import ExecutionPolicy
from app.domain.runtime_policy.operations import OperationsPolicy


class PolicyKind(StrEnum):
    EXECUTION = "execution"
    OPERATIONS = "operations"


class _RevisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    sequence: int = Field(ge=1)
    schema_version: int = Field(ge=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_by: str = Field(min_length=1, max_length=255)
    note: str = Field(min_length=1, max_length=1_000)
    restored_from_id: UUID | None = None
    created_at: datetime

    @field_validator("created_by", "note")
    @classmethod
    def normalize_non_empty_text(cls, value: str, info: object) -> str:
        return require_non_empty(value, field_name=getattr(info, "field_name", "value"))

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)


class ExecutionPolicyRevision(_RevisionModel):
    policy: ExecutionPolicy


class OperationsPolicyRevision(_RevisionModel):
    policy: OperationsPolicy


class RuntimePolicyHead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Literal["global"] = "global"
    version: int = Field(ge=1)
    execution_revision_id: UUID
    operations_revision_id: UUID
    updated_by: str = Field(min_length=1, max_length=255)
    updated_at: datetime

    @field_validator("updated_by")
    @classmethod
    def normalize_actor(cls, value: str) -> str:
        return require_non_empty(value, field_name="updated_by")

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)


class ActiveExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    head: RuntimePolicyHead
    revision: ExecutionPolicyRevision

    @model_validator(mode="after")
    def require_head_reference(self) -> "ActiveExecutionPolicy":
        if self.head.execution_revision_id != self.revision.id:
            raise ValueError("execution revision does not match the active head")
        return self


class ActiveOperationsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    head: RuntimePolicyHead
    revision: OperationsPolicyRevision

    @model_validator(mode="after")
    def require_head_reference(self) -> "ActiveOperationsPolicy":
        if self.head.operations_revision_id != self.revision.id:
            raise ValueError("operations revision does not match the active head")
        return self


class RuntimePolicyPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution: ActiveExecutionPolicy
    operations: ActiveOperationsPolicy

    @model_validator(mode="after")
    def require_one_head(self) -> "RuntimePolicyPair":
        if self.execution.head != self.operations.head:
            raise ValueError("active policies must share one head")
        return self


__all__ = [
    "ActiveExecutionPolicy",
    "ActiveOperationsPolicy",
    "ExecutionPolicyRevision",
    "OperationsPolicyRevision",
    "PolicyKind",
    "RuntimePolicyHead",
    "RuntimePolicyPair",
]
