from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.view_facts import attempt_key, to_view_fact
from app.domain.execution.events import StoredEvent


def event(kind, payload, version=1):
    return StoredEvent(
        position=7,
        event_id=UUID(int=7),
        stream_type="run",
        stream_id=str(UUID(int=1)),
        stream_version=1,
        event_type=kind,
        event_schema_version=version,
        public_payload=payload,
        internal_payload={"private": "hidden"},
        secret_ref=None,
        owner_user_id="u",
        team_id=None,
        correlation_id=UUID(int=1),
        causation_id=None,
        occurred_at=datetime(2026, 9, 7, tzinfo=UTC),
        prev_hash="0" * 64,
        event_hash="1" * 64,
    )


def test_reclaim_cannot_collapse_attempts():
    assert attempt_key("a", 1, 1) != attempt_key("a", 1, 2)
    assert attempt_key("a", 1, 2) == attempt_key("a", 1, 2)


def test_same_named_calls_have_distinct_steps_and_no_private_payload():
    a = to_view_fact(
        event(
            "ActivityRequested",
            {
                "activity_id": str(UUID(int=2)),
                "generation": 0,
                "activity_type": "tool",
                "public_data": {"tool_name": "search", "unexpected": "private"},
            },
        )
    )
    b = to_view_fact(
        event(
            "ActivityRequested",
            {
                "activity_id": str(UUID(int=3)),
                "generation": 0,
                "activity_type": "tool",
                "public_data": {"tool_name": "search"},
            },
        )
    )
    assert a.entity_id != b.entity_id
    assert a.patch["started_at"] is None
    assert a.patch["parent_step_id"] is None
    assert "private" not in str(a)


def test_legacy_completion_does_not_invent_start_or_attempt():
    fact = to_view_fact(
        event(
            "ActivityCompleted",
            {"activity_id": str(UUID(int=2)), "generation": 0, "public_data": {"success": False}},
        )
    )
    assert fact.patch["attempt_id"] is None
    assert "started_at" not in fact.patch
    assert fact.patch["status"] == "completed"
    assert fact.patch["business_outcome"] == "failure"


def test_fact_bundle_keeps_message_and_step_and_scrubs_text():
    from app.application.execution.view_facts import to_view_facts

    facts = to_view_facts(
        event(
            "ActivityCompleted",
            {
                "activity_id": str(UUID(int=2)),
                "generation": 0,
                "claim_generation": 2,
                "public_data": {
                    "kind": "message",
                    "message": "password=hidden object://private/ref",
                    "role": "assistant",
                },
            },
            2,
        )
    )
    assert [f.kind for f in facts] == ["step", "message"]
    assert "hidden" not in str(facts)
    assert "object://" not in str(facts)


def test_unknown_generation_is_not_zero_and_wait_reason_uses_formal_field():
    fact = to_view_fact(
        event("ActivityRequested", {"activity_id": str(UUID(int=2)), "activity_type": "tool"})
    )
    assert ":0:" not in fact.entity_id
    assert (
        to_view_fact(event("RunWaiting", {"reason": "approval"})).patch["wait_reason"] == "approval"
    )


def test_run_retry_events_are_visible_facts():
    assert (
        to_view_fact(event("RunAttemptFailed", {"failure_code": "failed"})).patch["status"]
        == "waiting"
    )
    assert to_view_fact(event("RunRetried", {})).patch["status"] == "queued"


@pytest.mark.parametrize("mode", ["recorded", "isolated"])
def test_evaluation_mode_comes_from_formal_admission_source(mode):
    fact = to_view_fact(
        event(
            "RunCreated",
            {
                "family": "agent",
                "source_entity_type": f"evaluation_{mode}_case",
                "source_entity_id": "fixed-result:0",
            },
        )
    )
    assert fact.patch["execution_mode"] == mode
    assert fact.playback_patch()["patch"]["execution_mode"] == mode
    assert "execution_mode" not in to_view_fact(event("RunCompleted", {})).patch


@pytest.mark.parametrize("source_type", ["session", "evaluation_judge", "recorded"])
def test_other_admission_sources_do_not_invent_evaluation_mode(source_type):
    fact = to_view_fact(
        event(
            "RunCreated",
            {"family": "agent", "source_entity_type": source_type, "source_entity_id": "source"},
        )
    )
    assert "execution_mode" not in fact.patch
    assert "execution_mode" not in fact.playback_patch()["patch"]


def test_journal_fact_rejects_unapproved_nested_payloads():
    import pytest

    from app.application.execution.view_facts import ProjectionFact

    with pytest.raises(ValueError, match="unapproved"):
        ProjectionFact(
            1, 0, None, "step", "s", {"input_payload": {"nested": "hidden"}}, "formal"
        ).playback_patch()
    with pytest.raises(ValueError, match="Extra inputs"):
        ProjectionFact(
            1,
            0,
            None,
            "run",
            "r",
            {"source": {"entity_id": "x", "entity_type": "session", "private": "hidden"}},
            "formal",
        ).playback_patch()


def test_terminal_step_fact_matches_frozen_public_dto():
    from app.application.dto.execution_view import StepView

    fact = to_view_fact(
        event(
            "ActivityCompleted",
            {
                "activity_id": str(UUID(int=2)),
                "generation": 0,
                "claim_generation": 2,
                "public_data": {"kind": "tool", "status": "failed"},
            },
        )
    )
    row = StepView.model_validate(
        {
            "step_id": fact.entity_id,
            "run_id": UUID(int=1),
            "kind": "tool",
            "projection_revision": 1,
            "completeness": {"state": "partial", "missing_fields": [], "missing_intervals": []},
            **fact.patch,
        }
    )
    assert row.status == "completed"
    assert row.business_outcome == "failure"
