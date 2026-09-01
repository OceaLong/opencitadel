from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.execution.run import RunFamily
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    RuntimePolicyHead,
    RuntimePolicyIntegrityError,
    policy_digest,
)


def _active_execution() -> ActiveExecutionPolicy:
    execution = ExecutionPolicy()
    execution_id = uuid4()
    operations_id = uuid4()
    now = datetime(2026, 8, 26, tzinfo=UTC)
    head = RuntimePolicyHead(
        version=7,
        execution_revision_id=execution_id,
        operations_revision_id=operations_id,
        updated_by="admin-1",
        updated_at=now,
    )
    return ActiveExecutionPolicy(
        head=head,
        revision=ExecutionPolicyRevision(
            id=execution_id,
            sequence=7,
            schema_version=1,
            policy=execution,
            digest=policy_digest(1, execution),
            created_by="admin-1",
            note="snapshot test",
            created_at=now,
        ),
    )


def test_every_run_family_derives_one_digest_valid_snapshot() -> None:
    from app.domain.runtime_policy.snapshot import (
        derive_run_policy_snapshot,
        validate_run_policy_snapshot,
    )

    active = _active_execution()

    for family in RunFamily:
        snapshot = derive_run_policy_snapshot(active, family)
        assert snapshot.family is family
        assert snapshot.family_policy.kind == family.value
        assert snapshot.execution_revision_id == active.revision.id
        assert snapshot.execution_policy_digest == active.revision.digest
        assert snapshot.snapshot_digest.startswith("sha256:")
        assert validate_run_policy_snapshot(snapshot) is snapshot


def test_snapshot_family_mismatch_fails_closed() -> None:
    from app.domain.runtime_policy.snapshot import (
        derive_run_policy_snapshot,
        validate_run_policy_snapshot,
    )

    snapshot = derive_run_policy_snapshot(_active_execution(), RunFamily.AGENT)
    changed = snapshot.model_copy(update={"family": RunFamily.PATROL})

    with pytest.raises(RuntimePolicyIntegrityError, match="family"):
        validate_run_policy_snapshot(changed)


def test_snapshot_digest_mismatch_fails_closed() -> None:
    from app.domain.runtime_policy.snapshot import (
        derive_run_policy_snapshot,
        validate_run_policy_snapshot,
    )

    snapshot = derive_run_policy_snapshot(_active_execution(), RunFamily.ASK)
    changed = snapshot.model_copy(update={"snapshot_digest": "sha256:" + "0" * 64})

    with pytest.raises(RuntimePolicyIntegrityError, match="digest"):
        validate_run_policy_snapshot(changed)


def test_agent_snapshot_contains_only_execution_subsets_needed_by_agent_runs() -> None:
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    snapshot = derive_run_policy_snapshot(_active_execution(), RunFamily.AGENT)
    payload = snapshot.family_policy.model_dump(mode="json")

    assert set(payload) == {
        "kind",
        "agent",
        "memory",
        "knowledge_retrieval",
    }
    assert "document" not in payload["knowledge_retrieval"]
    assert payload["knowledge_retrieval"]["graph_enabled"] is True
