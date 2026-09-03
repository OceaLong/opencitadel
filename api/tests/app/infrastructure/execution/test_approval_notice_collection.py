"""Approval lifecycle facts must reach a reviewer — including team-only Runs.

Unit-level pins for ``PostgresFormalProjector._collect_approval_notice``:
historically a purely team-owned Run (owner_user_id is None) produced no notice
at all, so its approval sat silent until the TTL cancelled the Run; and an
``ApprovalExpired`` fact notified nobody.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.execution.events import StoredEvent
from app.domain.execution.run import RunState
from app.infrastructure.execution.postgres_formal_projector import (
    ApprovalWaitingNotice,
    PostgresFormalProjector,
)

_STATE = RunState(
    run_id=UUID(int=1),
    source_entity_type="session",
    source_entity_id="session-1",
)


def _event(
    event_type: str = "ApprovalRequested",
    owner_user_id: str | None = "u1",
    team_id: str | None = None,
    payload: dict | None = None,
) -> StoredEvent:
    return StoredEvent(
        position=1,
        event_id=uuid4(),
        stream_type="run",
        stream_id=str(UUID(int=1)),
        stream_version=1,
        event_type=event_type,
        event_schema_version=1,
        public_payload=(
            payload
            if payload is not None
            else {"approval_id": str(UUID(int=2)), "subject_label": "write_external"}
        ),
        internal_payload={},
        secret_ref=None,
        owner_user_id=owner_user_id,
        team_id=team_id,
        correlation_id=uuid4(),
        causation_id=None,
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
        prev_hash="0" * 64,
        event_hash="1" * 64,
    )


def _collect(event: StoredEvent) -> list[ApprovalWaitingNotice]:
    notices: list[ApprovalWaitingNotice] = []
    PostgresFormalProjector._collect_approval_notice(event, _STATE, notices)
    return notices


def test_personal_scope_notifies_the_owner() -> None:
    notices = _collect(_event())
    assert [notice.user_id for notice in notices] == ["u1"]
    assert notices[0].team_id is None
    assert notices[0].kind == "approval_waiting"
    assert notices[0].session_id == "session-1"


def test_team_only_run_carries_team_id_for_reviewer_fanout() -> None:
    notices = _collect(_event(owner_user_id=None, team_id="t1"))
    assert notices[0].user_id is None
    assert notices[0].team_id == "t1"
    assert notices[0].kind == "approval_waiting"


def test_expired_approval_produces_expiry_notice() -> None:
    event = _event(
        event_type="ApprovalExpired",
        payload={"approval_id": str(UUID(int=2))},
    )
    notices = _collect(event)
    assert notices[0].kind == "approval_expired"
    assert notices[0].subject_label == ""


def test_other_events_produce_no_notice() -> None:
    assert _collect(_event(event_type="ApprovalDecided")) == []
