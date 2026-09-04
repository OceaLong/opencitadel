"""CI guards for the schema-evolution rules in app/domain/execution/EVOLUTION.md.

These tests pin behavior that, if it drifts silently, invalidates persisted
data: canonical hashing bytes, the RunState field set vs. its snapshot
serializer version, and the aggregate's registry/evolve coverage.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.execution.run import RunAggregate, RunState
from app.domain.execution.serialization import (
    canonical_json_bytes,
    canonical_state_hash,
)

# --- Golden canonical hashes -------------------------------------------------
#
# If either assertion fails without an intentional serialization change, a
# dependency upgrade (pydantic dump semantics, datetime formatting) has shifted
# the canonical byte form: every persisted event_hash / state_hash would then
# be judged corrupt. Fix the regression — do NOT re-pin these values casually.
# Re-pinning is only correct alongside a snapshot_serializer_version bump and
# (for event hashes) a data reset or explicit re-hash migration.

GOLDEN_STATE_HASH = "5c5fcbfaed49556bbdc98461f1f91a682640b1fb376224def230713797854717"
GOLDEN_JSON_HASH = "879a920caef5b535a825afec94c11844ae2b1027671deacea735423b426ba91b"

# --- RunState field set per serializer version -------------------------------
#
# Changing RunState's fields without bumping snapshot_serializer_version leaves
# stale snapshots parseable-but-wrong (or noisily self-healing under the
# corruption metric). When you change the field set: bump the version in
# RunAggregate AND record the new pair here.

RUN_STATE_FIELDS_BY_SERIALIZER_VERSION = {
    4: (
        "active_activity_ids",
        "activity_failure_codes",
        "activity_generations",
        "activity_results",
        "approval_decisions",
        "correlation_id",
        "failure_code",
        "family",
        "owner_user_id",
        "parent_run_id",
        "pending_approval_activity_id",
        "pending_approval_id",
        "policy_snapshot",
        "requested_activities",
        "result_ref",
        "retry_generation",
        "run_id",
        "semantic_payload",
        "settled_activities",
        "source_entity_id",
        "source_entity_type",
        "started_activity_ids",
        "status",
        "stream_version",
        "team_id",
        "terminal_event_id",
        "wait_reason",
    ),
    # v5 (2026-09-04): approval_decisions tuples gained a feedback slot
    # (clarification cards) — same field names, incompatible inner shape.
    5: (
        "active_activity_ids",
        "activity_failure_codes",
        "activity_generations",
        "activity_results",
        "approval_decisions",
        "correlation_id",
        "failure_code",
        "family",
        "owner_user_id",
        "parent_run_id",
        "pending_approval_activity_id",
        "pending_approval_id",
        "policy_snapshot",
        "requested_activities",
        "result_ref",
        "retry_generation",
        "run_id",
        "semantic_payload",
        "settled_activities",
        "source_entity_id",
        "source_entity_type",
        "started_activity_ids",
        "status",
        "stream_version",
        "team_id",
        "terminal_event_id",
        "wait_reason",
    ),
}


def test_canonical_state_hash_golden_bytes() -> None:
    state = RunState(
        run_id=UUID("10000000-0000-0000-0000-0000000000aa"),
        activity_results=((UUID(int=5), 0, "object://r", "sum", "sha256:" + "ab" * 32),),
        settled_activities=((UUID(int=5), "succeeded", 0),),
        stream_version=7,
        owner_user_id="user-golden",
    )

    assert canonical_state_hash(state) == GOLDEN_STATE_HASH


def test_canonical_json_bytes_golden_for_every_scalar_shape() -> None:
    value = {
        "s": "x",
        "i": 3,
        "f": 1.5,
        "b": True,
        "n": None,
        "dt": datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "u": UUID(int=9),
        "nested": {"list": [1, "two", {"k": "v"}]},
    }

    assert hashlib.sha256(canonical_json_bytes(value)).hexdigest() == GOLDEN_JSON_HASH


def test_run_state_field_set_matches_recorded_serializer_version() -> None:
    version = RunAggregate.snapshot_serializer_version
    recorded = RUN_STATE_FIELDS_BY_SERIALIZER_VERSION.get(version)

    assert recorded is not None, (
        f"snapshot_serializer_version {version} has no recorded RunState field "
        "set; record it in RUN_STATE_FIELDS_BY_SERIALIZER_VERSION"
    )
    assert tuple(sorted(RunState.model_fields)) == recorded, (
        "RunState's field set changed without bumping snapshot_serializer_version "
        "(see EVOLUTION.md)"
    )


def test_serializer_versions_are_never_reused() -> None:
    # A recorded historical version must not be re-recorded with new fields —
    # append a new version instead.
    assert len(RUN_STATE_FIELDS_BY_SERIALIZER_VERSION) == len(
        set(RUN_STATE_FIELDS_BY_SERIALIZER_VERSION)
    )
    assert max(RUN_STATE_FIELDS_BY_SERIALIZER_VERSION) == (RunAggregate.snapshot_serializer_version)


def test_aggregate_construction_self_checks_registry_coverage() -> None:
    # Constructing the aggregate runs _assert_registry_coverage; a divergence
    # between _EVENT_SPECS, _EVOLVED_EVENT_TYPES and the decide handlers would
    # raise here.
    aggregate = RunAggregate()

    assert aggregate.event_registry.registered_names()
    assert aggregate.command_registry.registered_names()
    for name in aggregate.command_registry.registered_names():
        assert callable(getattr(aggregate, f"_decide_{name}"))


def test_all_current_schemas_are_baseline_v1() -> None:
    # The 2026-09 greenfield reset: every event/command re-baselined at v1.
    # Future upgrades append versions with upcasters; latest_version moving
    # past 1 is expected then, but a baseline registered above 1 is not.
    aggregate = RunAggregate()
    for name in aggregate.event_registry.registered_names():
        assert aggregate.event_registry.latest_version(name) >= 1
    for name in aggregate.command_registry.registered_names():
        assert aggregate.command_registry.latest_version(name) >= 1


def test_evolution_rules_document_exists_and_names_this_guard() -> None:
    doc = Path(__file__).resolve().parents[4] / "app" / "domain" / "execution" / "EVOLUTION.md"
    content = doc.read_text(encoding="utf-8")

    assert "禁止重定基线" in content
    assert "test_schema_guards.py" in content


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_canonical_bytes_reject_non_finite_numbers(bad: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json_bytes({"x": bad})
