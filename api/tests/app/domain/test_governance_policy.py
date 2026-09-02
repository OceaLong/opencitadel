"""One immutable governance policy replaces split runtime policy families."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.runtime_policy.governance import (
    GovernancePolicy,
    GovernancePolicyRevision,
    QuotaLimits,
)


def _policy(**updates) -> GovernancePolicy:
    values = {
        "effect_timeout_seconds": 300,
        "effect_max_attempts": 3,
        "approval_ttl_seconds": 86_400,
        "worker_concurrency": 16,
        "retention_days": 30,
        "snapshot_interval": 50,
        "safety_overrides": {"tool.call": "non_idempotent_write"},
        "user_quota_defaults": {"monthly_model_tokens": 100_000},
        "team_quota_defaults": {"concurrent_runs": 10},
    }
    values.update(updates)
    return GovernancePolicy.model_validate(values)


def test_policy_digest_is_canonical_and_revision_is_immutable() -> None:
    first = _policy()
    second = GovernancePolicy.model_validate(
        dict(reversed(list(first.model_dump(mode="json").items())))
    )
    revision = GovernancePolicyRevision.create(
        revision_id=UUID(int=9100),
        policy=first,
        actor_user_id="admin-1",
        note="initial",
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert first.digest == second.digest == revision.digest
    with pytest.raises(ValidationError):
        revision.actor_user_id = "attacker"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effect_timeout_seconds", 0),
        ("effect_max_attempts", 0),
        ("approval_ttl_seconds", 0),
        ("worker_concurrency", 0),
        ("retention_days", 0),
        ("retention_days", 3651),
        ("snapshot_interval", 0),
    ],
)
def test_policy_rejects_unsafe_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _policy(**{field: value})


def test_safety_overrides_are_restricted_to_registered_effect_types() -> None:
    with pytest.raises(ValidationError, match="registered Effect"):
        _policy(safety_overrides={"network.exfiltrate": "read_only"})


def test_quota_dimensions_are_optional_but_never_negative() -> None:
    assert QuotaLimits().model_dump() == {
        "monthly_model_tokens": None,
        "daily_new_runs": None,
        "concurrent_runs": None,
        "storage_bytes": None,
    }
    with pytest.raises(ValidationError):
        QuotaLimits(storage_bytes=-1)
