"""Closed request and response schemas for Runtime Policy administration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _MutationRequest(_ClosedModel):
    expected_head_version: int = Field(ge=1)
    expected_active_revision_id: UUID
    note: str = Field(min_length=1, max_length=1_000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("note must not be blank")
        return normalized


class CreateExecutionPolicyRevisionRequest(_MutationRequest):
    policy: ExecutionPolicy


class CreateOperationsPolicyRevisionRequest(_MutationRequest):
    policy: OperationsPolicy


class RestorePolicyRevisionRequest(_MutationRequest):
    pass


class RuntimePolicyHeadResponse(_ClosedModel):
    id: str
    version: int
    execution_revision_id: UUID
    operations_revision_id: UUID
    updated_by: str
    updated_at: datetime

    @classmethod
    def from_domain(cls, head: RuntimePolicyHead) -> RuntimePolicyHeadResponse:
        return cls.model_validate(head.model_dump(mode="python"))


class ExecutionPolicyRevisionResponse(_ClosedModel):
    id: UUID
    sequence: int
    schema_version: int
    policy: ExecutionPolicy
    digest: str
    created_by: str
    note: str
    restored_from_id: UUID | None
    created_at: datetime

    @classmethod
    def from_domain(
        cls,
        revision: ExecutionPolicyRevision,
    ) -> ExecutionPolicyRevisionResponse:
        return cls.model_validate(revision.model_dump(mode="python"))


class OperationsPolicyRevisionResponse(_ClosedModel):
    id: UUID
    sequence: int
    schema_version: int
    policy: OperationsPolicy
    digest: str
    created_by: str
    note: str
    restored_from_id: UUID | None
    created_at: datetime

    @classmethod
    def from_domain(
        cls,
        revision: OperationsPolicyRevision,
    ) -> OperationsPolicyRevisionResponse:
        return cls.model_validate(revision.model_dump(mode="python"))


class ActiveExecutionPolicyResponse(_ClosedModel):
    head: RuntimePolicyHeadResponse
    revision: ExecutionPolicyRevisionResponse

    @classmethod
    def from_domain(cls, active: ActiveExecutionPolicy) -> ActiveExecutionPolicyResponse:
        return cls(
            head=RuntimePolicyHeadResponse.from_domain(active.head),
            revision=ExecutionPolicyRevisionResponse.from_domain(active.revision),
        )


class ActiveOperationsPolicyResponse(_ClosedModel):
    head: RuntimePolicyHeadResponse
    revision: OperationsPolicyRevisionResponse

    @classmethod
    def from_domain(cls, active: ActiveOperationsPolicy) -> ActiveOperationsPolicyResponse:
        return cls(
            head=RuntimePolicyHeadResponse.from_domain(active.head),
            revision=OperationsPolicyRevisionResponse.from_domain(active.revision),
        )


class ExecutionPolicyRevisionListResponse(_ClosedModel):
    items: list[ExecutionPolicyRevisionResponse]
    limit: int
    offset: int


class OperationsPolicyRevisionListResponse(_ClosedModel):
    items: list[OperationsPolicyRevisionResponse]
    limit: int
    offset: int


__all__ = [
    "ActiveExecutionPolicyResponse",
    "ActiveOperationsPolicyResponse",
    "CreateExecutionPolicyRevisionRequest",
    "CreateOperationsPolicyRevisionRequest",
    "ExecutionPolicyRevisionListResponse",
    "ExecutionPolicyRevisionResponse",
    "OperationsPolicyRevisionListResponse",
    "OperationsPolicyRevisionResponse",
    "RestorePolicyRevisionRequest",
    "RuntimePolicyHeadResponse",
]
