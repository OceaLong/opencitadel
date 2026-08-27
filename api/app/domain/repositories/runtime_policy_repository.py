"""Persistence contract for immutable Runtime Policy revisions."""

from typing import Protocol
from uuid import UUID

from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
    RuntimePolicyPair,
)


class RuntimePolicyRepository(Protocol):
    async def seed_if_missing(
        self,
        *,
        execution_policy: ExecutionPolicy,
        operations_policy: OperationsPolicy,
        actor: str,
        note: str,
    ) -> bool: ...

    async def load_head(self) -> RuntimePolicyHead: ...

    async def load_active_pair(self) -> RuntimePolicyPair: ...

    async def load_execution_revision(
        self,
        revision_id: UUID,
    ) -> ExecutionPolicyRevision: ...

    async def load_operations_revision(
        self,
        revision_id: UUID,
    ) -> OperationsPolicyRevision: ...

    async def list_execution_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[ExecutionPolicyRevision]: ...

    async def list_operations_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[OperationsPolicyRevision]: ...

    async def create_and_activate_execution(
        self,
        *,
        policy: ExecutionPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        actor: str,
        note: str,
        restored_from_id: UUID | None = None,
    ) -> ActiveExecutionPolicy: ...

    async def create_and_activate_operations(
        self,
        *,
        policy: OperationsPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        actor: str,
        note: str,
        restored_from_id: UUID | None = None,
    ) -> ActiveOperationsPolicy: ...


__all__ = ["RuntimePolicyRepository"]
