from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.execution.playback import reduce_facts
from app.domain.models.playback import PlaybackBoundary


def boundary(*, formal=10, progress=10, order=10, revision=10):
    return PlaybackBoundary(
        run_id=uuid4(),
        formal_position=formal,
        progress_position=progress,
        observed_order=order,
        projection_revision=revision,
        observed_at=datetime(2026, 9, 7, tzinfo=UTC),
        projector_version=1,
    )


def test_future_artifact_and_approval_are_not_visible():
    facts = [
        {"position": (1, 0), "kind": "approval", "id": "a", "patch": {"status": "pending"}},
        {"position": (2, 0), "kind": "approval", "id": "a", "patch": {"status": "approved"}},
        {"position": (3, 0), "kind": "artifact", "id": "doc:1", "patch": {"version": 1}},
    ]
    result = reduce_facts(facts, (1, 0))
    assert result["approval"]["a"]["status"] == "pending"
    assert result["artifact"] == {}


def test_dual_watermarks_are_checked_independently_not_lexicographically():
    facts = [
        {"position": (1, 50), "kind": "message", "id": "future-progress", "patch": {"text": "no"}},
        {"position": (9, 2), "kind": "message", "id": "future-formal", "patch": {"text": "no"}},
        {"position": (1, 2), "kind": "message", "id": "visible", "patch": {"text": "yes"}},
    ]
    assert set(reduce_facts(facts, (5, 5))["message"]) == {"visible"}


def test_observed_order_defines_consistent_prefix_for_equal_source_watermarks():
    cut = boundary(formal=5, progress=2, order=3, revision=3)
    facts = [
        {
            "position": (5, 2),
            "observed_order": 4,
            "kind": "message",
            "id": "future",
            "patch": {"text": "no"},
        },
        {
            "position": (5, 2),
            "observed_order": 3,
            "kind": "message",
            "id": "present",
            "patch": {"text": "yes"},
        },
    ]
    assert set(reduce_facts(facts, cut)["message"]) == {"present"}


def test_tombstone_removes_placeholder_before_replacement_and_replay_is_idempotent():
    facts = [
        {
            "position": (1, 0),
            "observed_order": 1,
            "kind": "step",
            "id": "placeholder",
            "patch": {"status": "queued"},
        },
        {
            "position": (2, 0),
            "observed_order": 2,
            "kind": "step",
            "id": "placeholder",
            "patch": {"removed": True, "replacement_step_id": "attempt"},
        },
        {
            "position": (2, 0),
            "observed_order": 2,
            "kind": "step",
            "id": "attempt",
            "patch": {"status": "running"},
        },
    ]
    once = reduce_facts(facts, boundary(formal=2, progress=0, order=2, revision=2))
    twice = reduce_facts(facts + facts, boundary(formal=2, progress=0, order=2, revision=2))
    assert once == twice
    assert once["step"] == {"attempt": {"status": "running"}}
    assert once["replacement_step_ids"] == {"placeholder": "attempt"}


def test_checkpoint_state_plus_incremental_matches_full_reduction():
    facts = [
        {
            "position": (1, 0),
            "observed_order": 1,
            "kind": "run",
            "id": "r",
            "patch": {"status": "running"},
        },
        {
            "position": (1, 1),
            "observed_order": 2,
            "kind": "message",
            "id": "m",
            "patch": {"text": "working"},
        },
        {
            "position": (2, 1),
            "observed_order": 3,
            "kind": "run",
            "id": "r",
            "patch": {"status": "completed"},
        },
    ]
    checkpoint = reduce_facts(facts[:2], boundary(formal=1, progress=1, order=2, revision=2))
    incremental = reduce_facts(
        facts[2:], boundary(formal=2, progress=1, order=3, revision=3), initial_state=checkpoint
    )
    assert incremental == reduce_facts(facts, boundary(formal=2, progress=1, order=3, revision=3))


def test_boundary_rejects_incoherent_revision_and_naive_time():
    with pytest.raises(ValueError, match="revision/order mismatch"):
        boundary(order=4, revision=3)
    with pytest.raises(ValueError, match="timezone-aware"):
        PlaybackBoundary(
            run_id=uuid4(),
            formal_position=1,
            progress_position=0,
            observed_order=1,
            projection_revision=1,
            observed_at=datetime(2026, 9, 7),  # noqa: DTZ001 - malformed input under test
            projector_version=1,
        )
