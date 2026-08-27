"""Small verified Runtime Policy reader used by application-boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
    policy_digest,
)


class MutablePolicyReader:
    def __init__(
        self,
        *,
        execution: ExecutionPolicy | None = None,
        operations: OperationsPolicy | None = None,
    ) -> None:
        self._execution = execution or ExecutionPolicy()
        self._operations = operations or OperationsPolicy()
        self._version = 0
        self.operations_calls: list[tuple[bool, datetime]] = []
        self.execution_calls: list[tuple[bool, datetime]] = []
        self.error: Exception | None = None
        self._activate()

    def set_operations(self, policy: OperationsPolicy) -> None:
        self._operations = policy
        self._activate()

    def set_execution(self, policy: ExecutionPolicy) -> None:
        self._execution = policy
        self._activate()

    async def active_operations(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveOperationsPolicy:
        self.operations_calls.append((require_fresh, now))
        if self.error is not None:
            raise self.error
        return self.operations

    async def active_execution(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveExecutionPolicy:
        self.execution_calls.append((require_fresh, now))
        if self.error is not None:
            raise self.error
        return self.execution

    def _activate(self) -> None:
        self._version += 1
        execution_id = uuid4()
        operations_id = uuid4()
        now = datetime(2026, 8, 26, tzinfo=UTC)
        head = RuntimePolicyHead(
            version=self._version,
            execution_revision_id=execution_id,
            operations_revision_id=operations_id,
            updated_by="test-admin",
            updated_at=now,
        )
        self.execution = ActiveExecutionPolicy(
            head=head,
            revision=ExecutionPolicyRevision(
                id=execution_id,
                sequence=self._version,
                schema_version=1,
                policy=self._execution,
                digest=policy_digest(1, self._execution),
                created_by="test-admin",
                note="test execution policy",
                created_at=now,
            ),
        )
        self.operations = ActiveOperationsPolicy(
            head=head,
            revision=OperationsPolicyRevision(
                id=operations_id,
                sequence=self._version,
                schema_version=1,
                policy=self._operations,
                digest=policy_digest(1, self._operations),
                created_by="test-admin",
                note="test operations policy",
                created_at=now,
            ),
        )


__all__ = ["MutablePolicyReader"]
