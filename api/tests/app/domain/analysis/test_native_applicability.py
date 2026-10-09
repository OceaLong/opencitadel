"""Native capture orchestration with SQL and E11 reads replaced at their I/O boundary."""

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.analysis.point_series import point_series
from app.domain.analysis.score_summary import score_summary
from app.domain.evaluation.summary import EvaluationSnapshot, SummaryRow
from app.infrastructure.repositories import db_analysis_points
from tests.app.domain.analysis.test_point_series import record
from tests.app.domain.analysis.test_score_applicability import records


@pytest.mark.asyncio
async def test_native_capture_keeps_sparse_applicable_members_and_fixed_revision(monkeypatch):
    facts = records()
    prototype = record()
    cases = {case: str(uuid4()) for case in "ABC"}
    for fact in facts:
        fact.update(
            result_id=str(uuid4()),
            case_id=cases[fact["case_id"]],
            batch_id=prototype["batch_id"],
            rubric=prototype["rubric_id"],
            config_id=prototype["row"]["config_id"],
            run_id=str(uuid4()),
        )
    before = deepcopy(facts)
    stored = []
    queries = []

    async def operation(db, scope, principal, **kwargs):
        if kwargs["operation"] == "prepare":
            return {"records": facts, "resources": []}
        if kwargs["operation"] == "store":
            stored.extend(kwargs["records"])
            return None
        assert kwargs["operation"] == "read"
        return {"records": stored}

    async def capture(self, scope, principal, batch, **query):
        queries.append((batch, query))
        now = datetime.fromisoformat(prototype["captured_at"])
        rows = []
        for fact in facts:
            row = dict(
                prototype["row"],
                id=fact["result_id"],
                case_id=fact["case_id"],
                run_id=fact["run_id"],
            )
            rows.append(SummaryRow.model_validate(row))
        # An E11 read may include other or superseded results; membership remains fixed.
        rows.append(SummaryRow.model_validate(prototype["row"]))
        return EvaluationSnapshot(
            id=prototype["snapshot_id"],
            batch_id=batch,
            evaluation_revision=query["evaluation_revision"],
            source=query["source"],
            dimension=query["dimension"],
            rubric_id=query["rubric_id"],
            captured_at=now,
            usage_watermark=now,
            expires_at=now + timedelta(minutes=15),
            rows=tuple(rows),
            allocations=(),
        ).model_dump(mode="json")

    monkeypatch.setattr(db_analysis_points, "points_operation", operation)
    monkeypatch.setattr(db_analysis_points.DBEvaluationSummaryRepository, "capture", capture)
    output = await db_analysis_points.capture_points(
        None, None, None, secret="unused", kind="analysis", capture="capture"
    )
    human = [series for series in point_series(output["records"]) if series["source"] == "human"]
    assert len(human) == 1
    assert len(human[0]["rows"]) == 9
    assert sum(row["value"] is not None for row in human[0]["rows"]) == 4
    assert sorted(row["value"] for row in human[0]["rows"] if row["value"] is not None) == [
        0,
        4,
        4,
        4,
    ]
    assert human[0]["identity"][-1] == ["quality"]
    assert {query["source"] for _, query in queries} == {"human", "model"}
    assert all(query["evaluation_revision"] == 4 for _, query in queries)
    assert facts == before
    summary = score_summary(facts)
    assert dict(summary["series"][0]["applicable_dimensions"])["human"] == ("quality",)
    assert summary["series"][0]["metrics"]["human:quality:coverage"]["value"] == pytest.approx(
        4 / 9
    )
