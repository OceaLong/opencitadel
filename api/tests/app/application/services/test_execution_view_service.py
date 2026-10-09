from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.execution_view_service import assemble_view
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope


def test_history_clips_duration_carries_progress_and_same_boundary_entities():
    run = uuid4()
    now = datetime.now(UTC)
    boundary = PlaybackBoundary(
        run_id=run,
        formal_position=2,
        progress_position=1,
        observed_order=3,
        projection_revision=3,
        observed_at=now,
        projector_version=1,
    )
    state = {
        "run": {
            str(run): {
                "family": "agent",
                "status": "running",
                "admitted_at": (now - timedelta(seconds=10)).isoformat(),
            }
        },
        "step": {
            "attempt": {
                "kind": "tool",
                "status": "running",
                "started_at": (now - timedelta(seconds=4)).isoformat(),
                "progress": 100,
                "progress_status": "completed",
            }
        },
        "approval": {"a": {"approval_id": "a", "status": "pending"}},
        "message": {"m": {"role": "assistant", "public_summary": "hello"}},
        "artifact": {"art": {"artifact_id": "art", "version": 1, "availability": "available"}},
    }
    page = assemble_view(
        scope=OwnerScope.personal("u"),
        boundary=boundary,
        state=state,
        latest_available=now + timedelta(seconds=20),
        missing_intervals=(),
    )
    assert page.run.duration_ms == 10000
    assert page.steps[0].duration_ms == 4000
    assert page.steps[0].ended_at is None
    assert page.steps[0].status == "running"
    assert page.steps[0].progress == 100
    assert page.steps[0].progress_status == "completed"
    assert page.approvals[0].approval_id == "a"
    assert page.messages[0].message_id == "m"
    assert page.artifacts[0].artifact_id == "art"
    assert page.run.latest_available > page.run.as_of
    assert page.revision == page.steps[0].projection_revision == 3


def test_gap_order_identity_survives_typed_completeness():
    run = uuid4()
    now = datetime.now(UTC)
    boundary = PlaybackBoundary(
        run_id=run,
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=now,
        projector_version=1,
    )
    page = assemble_view(
        scope=OwnerScope.personal("u"),
        boundary=boundary,
        state={"run": {str(run): {"family": "agent", "status": "queued"}}},
        latest_available=now,
        missing_intervals=(
            {
                "start": None,
                "end": None,
                "reason": "journal_observation_gap",
                "start_order": 2,
                "end_order": 4,
            },
        ),
    )
    assert page.run.completeness.missing_intervals[0].end_order == 4


def test_unknown_filter_values_are_rejected_instead_of_empty_success():
    from app.application.ports.execution_view import ViewCursorInvalid
    from app.application.services.execution_view_service import _filters

    with pytest.raises(ViewCursorInvalid):
        _filters({"state": "runing"}, "runs")
    with pytest.raises(ViewCursorInvalid):
        _filters({"family": "imaginary"}, "runs")
    assert _filters({"parent": "attempt"}, "steps") == {"parent_step_id": "attempt"}


def test_source_filters_require_both_identity_parts():
    from app.application.ports.execution_view import ViewCursorInvalid
    from app.application.services.execution_view_service import _filters

    expected = {"source_entity_type": "session", "source_entity_id": "s"}
    assert _filters(expected, "runs") == expected
    for filters in ({"source_entity_type": "session"}, {"source_entity_id": "s"}):
        with pytest.raises(ViewCursorInvalid):
            _filters(filters, "runs")


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [False, True])
async def test_step_cut_checks_current_run_scope_before_foreign_boundary(visible):
    from app.application.ports.execution_view import ViewCursorInvalid, ViewNotFound
    from app.application.services.execution_view_service import ExecutionViewService

    run = uuid4()
    boundary = PlaybackBoundary(
        run_id=run,
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime.now(UTC),
        projector_version=1,
    )

    @asynccontextmanager
    async def transaction(**_kwargs):
        yield "session"

    port = SimpleNamespace(
        transaction=transaction,
        capture_run=AsyncMock(
            return_value=boundary,
            side_effect=None if visible else ViewNotFound("run does not exist"),
        ),
        active_generation=AsyncMock(return_value="live"),
        prepare_read=AsyncMock(),
    )
    views = ExecutionViewService(port, cursor_secret=b"fixed-unit-test-cursor-secret")
    scope = OwnerScope.personal("reader")
    foreign_at = views._at(OwnerScope.team("owner", "team"), boundary, "live")
    with pytest.raises(ViewCursorInvalid if visible else ViewNotFound):
        await views.get_step_cut(scope, run, "step", foreign_at)
    port.capture_run.assert_awaited_once_with("session", scope, run)
    port.prepare_read.assert_not_awaited()
    port.active_generation.assert_not_awaited()
