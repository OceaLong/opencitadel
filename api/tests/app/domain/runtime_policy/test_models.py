from datetime import UTC, datetime
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError


def _runtime_policy_module():
    return import_module("app.domain.runtime_policy")


def test_execution_policy_is_closed_frozen_and_bounded() -> None:
    policy_module = _runtime_policy_module()
    execution_policy = policy_module.ExecutionPolicy

    with pytest.raises(ValidationError):
        execution_policy.model_validate({"agent": {"max_iterations": 12}, "unknown": True})
    with pytest.raises(ValidationError):
        execution_policy.model_validate({"agent": {"max_iterations": 0}})

    policy = execution_policy()
    with pytest.raises(ValidationError):
        policy.agent = policy_module.AgentExecutionPolicy(max_iterations=2)


def test_operations_policy_rejects_unbounded_or_inconsistent_values() -> None:
    policy_module = _runtime_policy_module()

    with pytest.raises(ValidationError):
        policy_module.OperationsPolicy.model_validate({"traffic": {"requests_per_minute": 0}})
    with pytest.raises(ValidationError):
        policy_module.OperationsPolicy.model_validate(
            {
                "scheduler": {
                    "max_concurrent_jobs_per_job": 3,
                }
            }
        )
    with pytest.raises(ValidationError):
        policy_module.OperationsPolicy.model_validate({"sandbox": {"memory_limit": "unbounded"}})


def test_traffic_policy_exposes_configurable_bounded_auth_budget() -> None:
    """The auth-specific per-minute budget is control-plane configurable through
    the same OperationsPolicy JSON mechanism, defaults tighter than the general
    budget, and stays bounded like every other traffic knob."""
    policy_module = _runtime_policy_module()

    default = policy_module.TrafficPolicy()
    assert default.auth_requests_per_minute == 10
    assert default.auth_requests_per_minute < default.requests_per_minute

    configured = policy_module.OperationsPolicy.model_validate(
        {"traffic": {"auth_requests_per_minute": 3}}
    )
    assert configured.traffic.auth_requests_per_minute == 3

    with pytest.raises(ValidationError):
        policy_module.OperationsPolicy.model_validate({"traffic": {"auth_requests_per_minute": 0}})


def test_source_access_policy_normalizes_and_rejects_overlap() -> None:
    policy_module = _runtime_policy_module()

    policy = policy_module.SourceAccessPolicy(
        url_allowlist=(" EXAMPLE.com ", "example.com", "Docs.Example.com"),
        url_denylist=("blocked.example.com",),
    )

    assert policy.url_allowlist == ("docs.example.com", "example.com")
    assert policy.url_denylist == ("blocked.example.com",)
    with pytest.raises(ValidationError, match="both allowed and denied"):
        policy_module.SourceAccessPolicy(
            url_allowlist=("example.com",),
            url_denylist=("EXAMPLE.COM",),
        )


def test_active_policy_pair_requires_one_consistent_head() -> None:
    policy_module = _runtime_policy_module()
    now = datetime(2026, 8, 25, tzinfo=UTC)
    execution_revision_id = uuid4()
    operations_revision_id = uuid4()
    head = policy_module.RuntimePolicyHead(
        version=1,
        execution_revision_id=execution_revision_id,
        operations_revision_id=operations_revision_id,
        updated_by="admin-1",
        updated_at=now,
    )
    execution_revision = policy_module.ExecutionPolicyRevision(
        id=execution_revision_id,
        sequence=1,
        schema_version=1,
        policy=policy_module.ExecutionPolicy(),
        digest="sha256:" + "a" * 64,
        created_by="admin-1",
        note="initial execution policy",
        created_at=now,
    )
    operations_revision = policy_module.OperationsPolicyRevision(
        id=operations_revision_id,
        sequence=1,
        schema_version=1,
        policy=policy_module.OperationsPolicy(),
        digest="sha256:" + "b" * 64,
        created_by="admin-1",
        note="initial operations policy",
        created_at=now,
    )
    execution = policy_module.ActiveExecutionPolicy(
        head=head,
        revision=execution_revision,
    )
    operations = policy_module.ActiveOperationsPolicy(
        head=head,
        revision=operations_revision,
    )

    pair = policy_module.RuntimePolicyPair(execution=execution, operations=operations)

    assert pair.execution.head.version == 1
    changed_head = head.model_copy(update={"version": 2})
    with pytest.raises(ValidationError, match="share one head"):
        policy_module.RuntimePolicyPair(
            execution=execution,
            operations=operations.model_copy(update={"head": changed_head}),
        )


def test_revision_and_head_timestamps_must_be_timezone_aware() -> None:
    policy_module = _runtime_policy_module()

    with pytest.raises(ValidationError, match="timezone-aware"):
        policy_module.RuntimePolicyHead(
            version=1,
            execution_revision_id=uuid4(),
            operations_revision_id=uuid4(),
            updated_by="admin-1",
            updated_at=datetime(2026, 8, 25, tzinfo=UTC).replace(tzinfo=None),
        )


def test_head_conflict_error_exposes_current_metadata_without_policy() -> None:
    policy_module = _runtime_policy_module()
    head = policy_module.RuntimePolicyHead(
        version=7,
        execution_revision_id=uuid4(),
        operations_revision_id=uuid4(),
        updated_by="admin-1",
        updated_at=datetime(2026, 8, 25, tzinfo=UTC),
    )

    error = policy_module.RuntimePolicyHeadConflictError(head)

    assert error.status_code == 409
    assert error.error_key == "runtimePolicy.headConflict"
    assert error.data["version"] == 7
    assert "policy" not in error.data


def test_runtime_policy_fail_closed_errors_have_stable_contracts() -> None:
    policy_module = _runtime_policy_module()

    integrity = policy_module.RuntimePolicyIntegrityError("digest mismatch")
    unavailable = policy_module.RuntimePolicyUnavailableError("head missing")
    stale = policy_module.RuntimePolicyStaleError(age_seconds=31.5)

    assert (integrity.status_code, integrity.error_key) == (
        503,
        "runtimePolicy.integrity",
    )
    assert (unavailable.status_code, unavailable.error_key) == (
        503,
        "runtimePolicy.unavailable",
    )
    assert (stale.status_code, stale.error_key, stale.data) == (
        503,
        "runtimePolicy.stale",
        {"age_seconds": 31.5},
    )
