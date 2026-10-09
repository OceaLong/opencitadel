"""Semantic cache boundaries without database or service startup."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from app.application.ports.execution_analysis import AnalysisQuery
from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.analysis.point_series import point_series
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)
from tests.app.domain.analysis.test_point_series import record


def test_old_point_semantics_are_unavailable_without_mutating_capture():
    old = record()
    old["identity"][5] = "execution-analysis-v1"
    with pytest.raises(ValueError, match="analysis_metric_version_unavailable"):
        point_series([old])
    assert old["identity"][5] == "execution-analysis-v1"


@pytest.mark.asyncio
async def test_old_watermark_cannot_be_relabelled_as_current_metrics(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.infrastructure.repositories.db_execution_analysis_repository.points_operation",
        AsyncMock(return_value={"captured_fingerprint": "points"}),
    )
    query = AnalysisQuery(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC), "day", "UTC", ()
    )

    class Repository(DBExecutionAnalysisRepository):
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield None

        async def _operation(self, db, scope, principal, operation, **payload):
            assert operation == "read"
            import json
            from dataclasses import asdict

            return {
                "query": json.loads(json.dumps(asdict(query), default=str)),
                "metrics": {"old": True},
                "watermark": "old",
                "authority_revision": 1,
                "manifest": "m",
            }

    with pytest.raises(ValueError, match="analysis_refresh_required"):
        await Repository(None, signing_secret="unused")._capture_once(None, None, query, "old")


@pytest.mark.asyncio
async def test_current_cache_query_and_body_keep_semantics_version(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.infrastructure.repositories.db_execution_analysis_repository.points_operation",
        AsyncMock(return_value={"captured_fingerprint": "points"}),
    )
    query = AnalysisQuery(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC), "day", "UTC", ()
    )
    queries = []

    class Repository(DBExecutionAnalysisRepository):
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield None

        async def _operation(self, db, scope, principal, operation, **payload):
            assert operation == "begin"
            queries.append(payload["query"])
            return {
                "metrics": {"metric_version": METRIC_VERSION},
                "watermark": "current",
                "authority_revision": 1,
                "manifest": "m",
            }

    captured = await Repository(None, signing_secret="unused")._capture_once(
        None, None, query, None
    )
    assert (
        queries[0]["metric_version"]
        == captured.metrics["metric_version"]
        == "execution-analysis-v2"
    )
    assert captured.watermark == "current"


@pytest.mark.asyncio
async def test_new_export_labels_recomputed_fixed_cut_metrics_with_current_semantics():
    from app.infrastructure.repositories.db_execution_export_repository import (
        DBExecutionExportRepository,
    )
    from tests.app.domain.analysis.test_score_applicability import records

    class Repository(DBExecutionExportRepository):
        async def _copy(self, db, scope, principal, accepted):
            return {
                "query": {
                    "start": "2026-09-01T00:00:00+00:00",
                    "end": "2026-09-02T00:00:00+00:00",
                    "grain": "day",
                    "timezone": "UTC",
                    "filters": [],
                },
                "facts": {
                    "run_groups": [],
                    "physical": [],
                    "intervals": [],
                    "approvals": [],
                    "coverage": {},
                    "captured_at": "2026-09-01T00:00:00+00:00",
                    "accounting_run_count": 0,
                    "allocations": [],
                    "score_records": records(),
                    "accounting_coverage": {},
                },
                "body": {"captured_at": "2026-09-01T00:00:00+00:00"},
                "row_count": 9,
                "expires_at": "2026-09-02T00:00:00+00:00",
            }

        async def _operation(self, db, scope, principal, operation, **payload):
            assert operation == "seal"
            return payload["header"]

    header = await Repository(None, signing_secret="unused")._seal(
        None,
        None,
        None,
        {"id": "export"},
        {
            "source_kind": "comparison",
            "comparison_id": "comparison",
            "revision": 1,
            "format": "json",
        },
    )
    assert (
        header["metadata"]["metric_version"]
        == header["metrics"]["metric_version"]
        == "execution-analysis-v2"
    )
    assert header["metrics"]["scores"]["evaluation_cuts"] == [
        {"batch_id": "batch", "evaluation_revision": 4}
    ]
    assert header["metrics"]["scores"]["series"][0]["metrics"]["human:quality:coverage"][
        "value"
    ] == pytest.approx(4 / 9)
