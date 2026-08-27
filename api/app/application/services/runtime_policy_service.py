"""Administrator-facing Runtime Policy revision orchestration."""

from __future__ import annotations

from uuid import UUID

from app.application.ports.streams import RuntimePolicyHintPublisher
from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from app.domain.repositories.runtime_policy_repository import RuntimePolicyRepository
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
)


class RuntimePolicyService:
    """Owns typed admin reads and post-commit mutation side effects."""

    def __init__(
        self,
        *,
        repository: RuntimePolicyRepository,
        audit_service: AuditService,
        hint_publisher: RuntimePolicyHintPublisher,
    ) -> None:
        self._repository = repository
        self._audit = audit_service
        self._hint_publisher = hint_publisher

    async def get_active_execution(self) -> ActiveExecutionPolicy:
        return (await self._repository.load_active_pair()).execution

    async def get_active_operations(self) -> ActiveOperationsPolicy:
        return (await self._repository.load_active_pair()).operations

    async def list_execution_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[ExecutionPolicyRevision]:
        return await self._repository.list_execution_revisions(limit=limit, offset=offset)

    async def list_operations_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[OperationsPolicyRevision]:
        return await self._repository.list_operations_revisions(limit=limit, offset=offset)

    async def create_execution(
        self,
        *,
        policy: ExecutionPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        note: str,
        actor_user_id: str,
    ) -> ActiveExecutionPolicy:
        active = await self._repository.create_and_activate_execution(
            policy=policy,
            expected_head_version=expected_head_version,
            expected_active_revision_id=expected_active_revision_id,
            actor=actor_user_id,
            note=note,
            restored_from_id=None,
        )
        await self._after_execution_mutation(active, actor_user_id=actor_user_id)
        return active

    async def create_operations(
        self,
        *,
        policy: OperationsPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        note: str,
        actor_user_id: str,
    ) -> ActiveOperationsPolicy:
        active = await self._repository.create_and_activate_operations(
            policy=policy,
            expected_head_version=expected_head_version,
            expected_active_revision_id=expected_active_revision_id,
            actor=actor_user_id,
            note=note,
            restored_from_id=None,
        )
        await self._after_operations_mutation(active, actor_user_id=actor_user_id)
        return active

    async def restore_execution(
        self,
        *,
        revision_id: UUID,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        note: str,
        actor_user_id: str,
    ) -> ActiveExecutionPolicy:
        source = await self._repository.load_execution_revision(revision_id)
        active = await self._repository.create_and_activate_execution(
            policy=source.policy,
            expected_head_version=expected_head_version,
            expected_active_revision_id=expected_active_revision_id,
            actor=actor_user_id,
            note=note,
            restored_from_id=source.id,
        )
        await self._after_execution_mutation(active, actor_user_id=actor_user_id)
        return active

    async def restore_operations(
        self,
        *,
        revision_id: UUID,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        note: str,
        actor_user_id: str,
    ) -> ActiveOperationsPolicy:
        source = await self._repository.load_operations_revision(revision_id)
        active = await self._repository.create_and_activate_operations(
            policy=source.policy,
            expected_head_version=expected_head_version,
            expected_active_revision_id=expected_active_revision_id,
            actor=actor_user_id,
            note=note,
            restored_from_id=source.id,
        )
        await self._after_operations_mutation(active, actor_user_id=actor_user_id)
        return active

    async def _after_execution_mutation(
        self,
        active: ActiveExecutionPolicy,
        *,
        actor_user_id: str,
    ) -> None:
        await self._record_mutation(
            kind="execution",
            active=active,
            actor_user_id=actor_user_id,
        )

    async def _after_operations_mutation(
        self,
        active: ActiveOperationsPolicy,
        *,
        actor_user_id: str,
    ) -> None:
        await self._record_mutation(
            kind="operations",
            active=active,
            actor_user_id=actor_user_id,
        )

    async def _record_mutation(
        self,
        *,
        kind: str,
        active: ActiveExecutionPolicy | ActiveOperationsPolicy,
        actor_user_id: str,
    ) -> None:
        restored_from_id = active.revision.restored_from_id
        action = "restore" if restored_from_id is not None else "activate"
        await self._audit.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=f"runtime_policy.{kind}.{action}",
                resource_type=f"runtime_policy_{kind}_revision",
                resource_id=str(active.revision.id),
                metadata={
                    "head_version": active.head.version,
                    "revision_id": str(active.revision.id),
                    "digest": active.revision.digest,
                    "restored_from_id": (
                        str(restored_from_id) if restored_from_id is not None else None
                    ),
                },
            )
        )
        try:
            await self._hint_publisher.publish_changed(active.head.version)
        except (OSError, RuntimeError, ValueError):
            # PostgreSQL revision and audit writes are already authoritative.
            return


__all__ = ["RuntimePolicyService"]
