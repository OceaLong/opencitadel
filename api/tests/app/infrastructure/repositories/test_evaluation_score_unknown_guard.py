"""Unit guards only: no PostgreSQL persistence or history-join claim."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.evaluation.scoring import ScoreValue
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_evaluation_score_repository import (
    DBEvaluationScoreRepository,
)


class RubricBoundaryReached(Exception):
    """Stop after the unknown guard, before any persistent score writes."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection", "score_status", "score_value", "current_error", "allowed"),
    [
        (
            {
                "terminal": True,
                "status": "failed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "error",
            None,
            None,
            True,
        ),
        (None, "error", None, None, False),
        (
            {
                "terminal": False,
                "status": "failed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "error",
            None,
            None,
            False,
        ),
        (
            {
                "terminal": True,
                "status": "completed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "error",
            None,
            None,
            False,
        ),
        (
            {"terminal": True, "status": "failed", "state": {"failure_code": "MODEL_CALL_FAILED"}},
            "error",
            None,
            None,
            False,
        ),
        ({"terminal": True, "status": "failed", "state": {}}, "error", None, None, False),
        (
            {
                "terminal": True,
                "status": "failed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "valid",
            1,
            None,
            False,
        ),
        (
            {
                "terminal": True,
                "status": "failed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "not_evaluable",
            None,
            None,
            False,
        ),
        (
            {
                "terminal": True,
                "status": "failed",
                "state": {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            },
            "error",
            None,
            "source_revoked",
            False,
        ),
    ],
)
async def test_unsafe_judge_accepts_only_current_exact_terminal_error_null(
    projection, score_status, score_value, current_error, allowed, monkeypatch
):
    scope, principal = OwnerScope.personal("owner"), Principal(user_id="owner")
    rubric_id, judge_run_id = uuid4(), uuid4()
    candidate = SimpleNamespace(
        batch_id=uuid4(),
        result_id=uuid4(),
        run_id=uuid4(),
        suite_version_id=uuid4(),
        model_dump=lambda **_: {"result_id": "candidate"},
    )
    intent = {
        "id": uuid4(),
        "status": "submitted",
        "result_id": candidate.result_id,
        "candidate": {"run_id": str(candidate.run_id)},
        "rubric_id": rubric_id,
    }

    @asynccontextmanager
    async def nested():
        yield

    result = SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
    db = SimpleNamespace(begin_nested=nested, execute=AsyncMock(return_value=result))
    judges = SimpleNamespace(
        get=AsyncMock(return_value=intent),
        current=AsyncMock(side_effect=PermissionError(current_error) if current_error else None),
        unsafe=AsyncMock(return_value=True),
        projection=AsyncMock(return_value=projection),
    )
    config = SimpleNamespace(
        get_version=AsyncMock(
            side_effect=[{"rubric_version": str(rubric_id)}, RubricBoundaryReached()]
        )
    )
    work = SimpleNamespace(
        db_session=db,
        evaluation_batch=SimpleNamespace(
            lock=AsyncMock(),
            get=AsyncMock(return_value={"principal": principal.model_dump(mode="json")}),
        ),
        evaluation_dataset=SimpleNamespace(authorize=AsyncMock()),
        evaluation_configuration=config,
        evaluation_judge=judges,
    )
    repo = DBEvaluationScoreRepository(work)
    monkeypatch.setattr(repo, "eligible", AsyncMock())
    score = ScoreValue(
        source="model",
        dimension="correctness",
        rubric_revision=rubric_id,
        value=score_value,
        status=score_status,
        reason="judge_execution_failed",
    )
    expected_type = (
        RubricBoundaryReached if allowed else PermissionError if current_error else ValueError
    )
    expected_reason = None if allowed else current_error or "judge_effect_unknown"
    with pytest.raises(expected_type, match=expected_reason):
        await repo.append(
            scope,
            principal,
            candidate,
            source="model",
            scores=[score],
            expected_evaluation_revision=0,
            request_id="judge:" + str(intent["id"]),
            required_dimensions=(),
            applicable_dimensions=("correctness",),
            judge_run_id=judge_run_id,
        )
    judges.current.assert_awaited_once_with(scope, intent)
    if current_error:
        judges.unsafe.assert_not_awaited()
    else:
        judges.unsafe.assert_awaited_once_with(scope, intent, include_unresolved=False)
        judges.projection.assert_awaited_once_with(scope, intent)
    # Only the idempotency read ran; rejected values never reached INSERT/CAS.
    assert db.execute.await_count == 1
    assert config.get_version.await_count == (2 if allowed else 1)
