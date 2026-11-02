"""Installation defaults must not change immutable schema-v1 history."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.domain.execution.run import RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ExecutionPolicy,
    KnowledgeBaseExecutionPolicy,
    OperationsPolicy,
    RunPolicySnapshot,
    RuntimePolicyHead,
    derive_run_policy_snapshot,
    initial_execution_policy,
    policy_digest,
    validate_run_policy_snapshot,
)
from app.infrastructure.models.runtime_policy import (
    ExecutionPolicyRevisionORM,
    OperationsPolicyRevisionORM,
    RuntimePolicyHeadORM,
)
from app.infrastructure.repositories.postgres_runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)


def test_initial_policy_serializes_a_complete_basic_knowledge_policy():
    policy = initial_execution_policy()
    payload = policy.model_dump(mode="json")
    knowledge = payload["knowledge_base"]

    assert set(payload) == set(ExecutionPolicy.model_fields)
    assert set(knowledge) == set(KnowledgeBaseExecutionPolicy.model_fields)
    assert knowledge["vector_enabled"] is False
    assert knowledge["rerank"]["enabled"] is False
    assert knowledge["graphrag"]["enabled"] is False
    assert knowledge["ocr"]["mode"] == "off"
    restored = ExecutionPolicy.model_validate(payload)
    assert restored.model_dump(mode="json") == payload
    assert policy_digest(1, restored) == policy_digest(1, policy)


def _legacy_payload():
    # Schema-v1 decoding historically supplies these enhanced defaults. A
    # sparse payload with a digest of the decoded policy must still round-trip.
    policy = ExecutionPolicy()
    payload = policy.model_dump(mode="json")
    knowledge = payload["knowledge_base"]
    knowledge.pop("vector_enabled")
    knowledge["rerank"].pop("enabled")
    knowledge["graphrag"].pop("enabled")
    knowledge["ocr"].pop("mode")
    return policy, payload


def _revision_row(policy, payload):
    return SimpleNamespace(
        id=uuid4(),
        sequence=4,
        schema_version=1,
        payload=payload,
        digest=policy_digest(1, policy),
        created_by="existing-admin",
        note="existing immutable revision",
        restored_from_id=None,
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def test_existing_sparse_execution_revision_keeps_enhancements_and_digest():
    policy, payload = _legacy_payload()
    row = _revision_row(policy, payload)
    original = deepcopy(payload)

    revision = PostgresRuntimePolicyRepository._execution_revision(row)

    assert revision.policy.knowledge_base.vector_enabled is True
    assert revision.policy.knowledge_base.rerank.enabled is True
    assert revision.policy.knowledge_base.graphrag.enabled is True
    assert revision.policy.knowledge_base.ocr.mode == "vision_llm"
    assert revision.digest == policy_digest(1, policy)
    assert row.payload == original


def test_existing_sparse_ingest_snapshot_keeps_its_original_digest():
    policy, _payload = _legacy_payload()
    row = _revision_row(policy, policy.model_dump(mode="json"))
    head = RuntimePolicyHead(
        version=4,
        execution_revision_id=row.id,
        operations_revision_id=uuid4(),
        updated_by=row.created_by,
        updated_at=row.created_at,
    )
    active = ActiveExecutionPolicy(
        head=head,
        revision=PostgresRuntimePolicyRepository._execution_revision(row),
    )
    snapshot = derive_run_policy_snapshot(active, RunFamily.KB_INGEST)
    payload = snapshot.model_dump(mode="json")
    knowledge = payload["family_policy"]["knowledge_base"]
    knowledge.pop("vector_enabled")
    knowledge["rerank"].pop("enabled")
    knowledge["graphrag"].pop("enabled")
    knowledge["ocr"].pop("mode")

    restored = RunPolicySnapshot.model_validate(payload)

    assert validate_run_policy_snapshot(restored) is restored
    assert restored.snapshot_digest == snapshot.snapshot_digest
    assert restored.family_policy.knowledge_base == policy.knowledge_base


@pytest.mark.asyncio
async def test_new_seed_validates_existing_head_without_writing_any_revision(monkeypatch):
    policy, payload = _legacy_payload()
    execution = _revision_row(policy, payload)
    operations = OperationsPolicy()
    operations_row = _revision_row(operations, operations.model_dump(mode="json"))
    head = SimpleNamespace(
        id="global",
        version=4,
        execution_revision_id=execution.id,
        operations_revision_id=operations_row.id,
        updated_by="existing-admin",
        updated_at=execution.created_at,
    )
    rows = {
        (RuntimePolicyHeadORM, "global"): head,
        (ExecutionPolicyRevisionORM, execution.id): execution,
        (OperationsPolicyRevisionORM, operations_row.id): operations_row,
    }

    class Session:
        def __init__(self):
            self.execute = AsyncMock()
            self.get = AsyncMock(side_effect=lambda model, key: rows[(model, key)])
            self.scalar = AsyncMock()
            self.add_all = Mock()
            self.flush = AsyncMock()
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    session = Session()
    repository = PostgresRuntimePolicyRepository(
        session_factory=lambda: session,
        authorization=AuthorizationContext.system("migration:runtime-policy-seed"),
    )
    monkeypatch.setattr(repository, "_authorize", AsyncMock())
    original_payload = deepcopy(execution.payload)
    original_digest = execution.digest

    created = await repository.seed_if_missing(
        execution_policy=initial_execution_policy(),
        operations_policy=OperationsPolicy(),
        actor="migration:runtime-policy-seed",
        note="new installation defaults",
    )

    assert created is False
    assert head.version == 4
    assert head.execution_revision_id == execution.id
    assert execution.payload == original_payload
    assert execution.digest == original_digest
    session.scalar.assert_not_awaited()
    session.add_all.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    # The only SQL statement is the existing seed's advisory lock.
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_empty_repository_persists_the_complete_initial_policy_atomically(monkeypatch):
    class Session:
        execute = AsyncMock()
        get = AsyncMock(return_value=None)
        scalar = AsyncMock(return_value=0)
        add_all = Mock()
        add = Mock()
        flush = AsyncMock()
        commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    session = Session()
    repository = PostgresRuntimePolicyRepository(
        session_factory=lambda: session,
        authorization=AuthorizationContext.system("migration:runtime-policy-seed"),
    )
    monkeypatch.setattr(repository, "_authorize", AsyncMock())
    policy = initial_execution_policy()

    assert (
        await repository.seed_if_missing(
            execution_policy=policy,
            operations_policy=OperationsPolicy(),
            actor="migration:runtime-policy-seed",
            note="initial installation defaults",
        )
        is True
    )

    execution, operations = session.add_all.call_args.args[0]
    head = session.add.call_args.args[0]
    assert isinstance(execution, ExecutionPolicyRevisionORM)
    assert execution.payload == policy.model_dump(mode="json")
    assert execution.digest == policy_digest(1, policy)
    assert isinstance(operations, OperationsPolicyRevisionORM)
    assert head.version == 1
    assert head.execution_revision_id == execution.id
    assert head.operations_revision_id == operations.id
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
