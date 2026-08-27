from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.services.runtime_policy_service import RuntimePolicyService
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    AgentExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
    RuntimePolicyPair,
    policy_digest,
)

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)


def _pair(*, version: int = 1) -> RuntimePolicyPair:
    execution_id = uuid4()
    operations_id = uuid4()
    execution = ExecutionPolicy()
    operations = OperationsPolicy()
    head = RuntimePolicyHead(
        version=version,
        execution_revision_id=execution_id,
        operations_revision_id=operations_id,
        updated_by="seed",
        updated_at=NOW,
    )
    return RuntimePolicyPair(
        execution=ActiveExecutionPolicy(
            head=head,
            revision=ExecutionPolicyRevision(
                id=execution_id,
                sequence=version,
                schema_version=1,
                policy=execution,
                digest=policy_digest(1, execution),
                created_by="seed",
                note="execution seed",
                created_at=NOW,
            ),
        ),
        operations=ActiveOperationsPolicy(
            head=head,
            revision=OperationsPolicyRevision(
                id=operations_id,
                sequence=version,
                schema_version=1,
                policy=operations,
                digest=policy_digest(1, operations),
                created_by="seed",
                note="operations seed",
                created_at=NOW,
            ),
        ),
    )


class _Repository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.pair = _pair()
        self.execution_kwargs: dict | None = None
        self.operations_kwargs: dict | None = None

    async def load_active_pair(self) -> RuntimePolicyPair:
        return self.pair

    async def load_execution_revision(self, revision_id: UUID) -> ExecutionPolicyRevision:
        assert revision_id == self.pair.execution.revision.id
        return self.pair.execution.revision

    async def load_operations_revision(self, revision_id: UUID) -> OperationsPolicyRevision:
        assert revision_id == self.pair.operations.revision.id
        return self.pair.operations.revision

    async def list_execution_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[ExecutionPolicyRevision]:
        assert (limit, offset) == (25, 5)
        return [self.pair.execution.revision]

    async def list_operations_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[OperationsPolicyRevision]:
        assert (limit, offset) == (25, 5)
        return [self.pair.operations.revision]

    async def create_and_activate_execution(self, **kwargs) -> ActiveExecutionPolicy:
        self.events.append("commit")
        self.execution_kwargs = kwargs
        source = self.pair
        revision_id = uuid4()
        head = source.execution.head.model_copy(
            update={
                "version": source.execution.head.version + 1,
                "execution_revision_id": revision_id,
                "updated_by": kwargs["actor"],
            }
        )
        policy = kwargs["policy"]
        return ActiveExecutionPolicy(
            head=head,
            revision=ExecutionPolicyRevision(
                id=revision_id,
                sequence=2,
                schema_version=1,
                policy=policy,
                digest=policy_digest(1, policy),
                created_by=kwargs["actor"],
                note=kwargs["note"],
                restored_from_id=kwargs["restored_from_id"],
                created_at=NOW,
            ),
        )

    async def create_and_activate_operations(self, **kwargs) -> ActiveOperationsPolicy:
        self.events.append("commit")
        self.operations_kwargs = kwargs
        source = self.pair
        revision_id = uuid4()
        head = source.operations.head.model_copy(
            update={
                "version": source.operations.head.version + 1,
                "operations_revision_id": revision_id,
                "updated_by": kwargs["actor"],
            }
        )
        policy = kwargs["policy"]
        return ActiveOperationsPolicy(
            head=head,
            revision=OperationsPolicyRevision(
                id=revision_id,
                sequence=2,
                schema_version=1,
                policy=policy,
                digest=policy_digest(1, policy),
                created_by=kwargs["actor"],
                note=kwargs["note"],
                restored_from_id=kwargs["restored_from_id"],
                created_at=NOW,
            ),
        )


class _Audit:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.logs = []

    async def record(self, log) -> None:
        self.events.append("audit")
        self.logs.append(log)


class _Publisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.versions: list[int] = []

    async def publish_changed(self, head_version: int) -> None:
        self.events.append("hint")
        self.versions.append(head_version)


def _service() -> tuple[RuntimePolicyService, _Repository, _Audit, _Publisher, list[str]]:
    events: list[str] = []
    repository = _Repository(events)
    audit = _Audit(events)
    publisher = _Publisher(events)
    return (
        RuntimePolicyService(
            repository=repository,
            audit_service=audit,
            hint_publisher=publisher,
        ),
        repository,
        audit,
        publisher,
        events,
    )


@pytest.mark.asyncio
async def test_create_execution_commits_then_audits_then_hints() -> None:
    service, repository, audit, publisher, events = _service()
    policy = ExecutionPolicy(agent=AgentExecutionPolicy(max_iterations=19))

    active = await service.create_execution(
        policy=policy,
        expected_head_version=1,
        expected_active_revision_id=repository.pair.execution.revision.id,
        note="raise bounded agent budget",
        actor_user_id="admin-7",
    )

    assert events == ["commit", "audit", "hint"]
    assert repository.execution_kwargs == {
        "policy": policy,
        "expected_head_version": 1,
        "expected_active_revision_id": repository.pair.execution.revision.id,
        "actor": "admin-7",
        "note": "raise bounded agent budget",
        "restored_from_id": None,
    }
    assert publisher.versions == [active.head.version]
    log = audit.logs[0]
    assert log.actor_user_id == "admin-7"
    assert log.action == "runtime_policy.execution.activate"
    assert log.resource_id == str(active.revision.id)
    assert log.metadata == {
        "head_version": active.head.version,
        "revision_id": str(active.revision.id),
        "digest": active.revision.digest,
        "restored_from_id": None,
    }
    assert "policy" not in log.metadata


@pytest.mark.asyncio
async def test_restore_creates_a_distinct_audited_revision_from_source() -> None:
    service, repository, audit, _, events = _service()
    source = repository.pair.operations.revision

    active = await service.restore_operations(
        revision_id=source.id,
        expected_head_version=repository.pair.operations.head.version,
        expected_active_revision_id=source.id,
        note="restore known-good operations limits",
        actor_user_id="admin-8",
    )

    assert active.revision.id != source.id
    assert active.revision.policy == source.policy
    assert repository.operations_kwargs is not None
    assert repository.operations_kwargs["restored_from_id"] == source.id
    assert events == ["commit", "audit", "hint"]
    assert audit.logs[0].action == "runtime_policy.operations.restore"
    assert audit.logs[0].metadata["restored_from_id"] == str(source.id)


@pytest.mark.asyncio
async def test_reads_return_consistent_active_pair_and_typed_history() -> None:
    service, repository, _, _, events = _service()

    assert await service.get_active_execution() == repository.pair.execution
    assert await service.get_active_operations() == repository.pair.operations
    assert await service.list_execution_revisions(limit=25, offset=5) == [
        repository.pair.execution.revision
    ]
    assert await service.list_operations_revisions(limit=25, offset=5) == [
        repository.pair.operations.revision
    ]
    assert events == []
