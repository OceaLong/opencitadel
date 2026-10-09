"""Public read contracts reject ambiguity and retain unavailable evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError


def run_data():
    return {
        "run_id": uuid4(),
        "family": "agent",
        "status": "running",
        "wait_reason": None,
        "scope": {"owner_user_id": "u", "team_id": None},
        "source": None,
        "purpose": "production",
        "projection_revision": 3,
        "as_of": "2026-09-07T08:00:00+08:00",
        "latest_available": "2026-09-07T00:00:00Z",
        "completeness": {"state": "partial", "missing_fields": ["source"], "missing_intervals": []},
        "capabilities": [],
    }


def test_run_normalizes_utc_and_preserves_unknowns():
    from app.application.dto.execution_view import RunView

    view = RunView(**run_data())
    assert view.as_of == datetime(2026, 9, 7, tzinfo=UTC)
    assert view.source is None
    assert view.admitted_at is None
    assert view.model_dump(mode="json")["run_id"] == str(view.run_id)
    assert view.model_dump(mode="json")["as_of"].endswith("Z")


@pytest.mark.parametrize(
    "patch",
    [
        {"private_payload": {}},
        {"as_of": "2026-09-07T00:00:00"},
        {"scope": {"owner_user_id": "u", "team_id": "t"}},
        {"projection_revision": -1},
        {"status": "invented"},
    ],
)
def test_run_rejects_private_or_ambiguous_data(patch):
    from app.application.dto.execution_view import RunView

    with pytest.raises(ValidationError):
        RunView(**(run_data() | patch))


def test_steps_keep_missing_relations_and_times_null():
    from app.application.dto.execution_view import StepView

    step = StepView(
        step_id="step",
        run_id=uuid4(),
        kind="tool",
        status="unknown",
        projection_revision=3,
        completeness={
            "state": "partial",
            "missing_fields": ["started_at"],
            "missing_intervals": [],
        },
    )
    assert step.duration_ms is None
    assert step.started_at is None
    assert step.attempt_id is None
    assert step.parent_step_id is None
    assert step.artifact_refs is None
    assert step.citation_refs is None
    with pytest.raises(ValidationError):
        StepView(**(step.model_dump() | {"parent": step.model_dump()}))


def test_page_requires_same_run_and_revision():
    from app.application.dto.execution_view import RunView, StepView, ViewPage

    run = RunView(**run_data())
    step = StepView(
        step_id="s",
        run_id=run.run_id,
        kind="model",
        status="running",
        projection_revision=3,
        completeness=run.completeness,
    )
    assert ViewPage(run=run, steps=[step], next_cursor=None, revision=3).revision == 3
    for patch in (
        {"revision": 4},
        {"steps": [step.model_copy(update={"run_id": uuid4()})]},
        {"steps": [step.model_copy(update={"projection_revision": 2})]},
    ):
        with pytest.raises(ValidationError):
            ViewPage(**({"run": run, "steps": [step], "next_cursor": None, "revision": 3} | patch))
