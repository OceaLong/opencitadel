"""Historical analysis pages retain native facts for every selected UTC bucket."""

# ruff: noqa: F401,F811
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.ports.execution_analysis import AnalysisQuery
from app.interfaces.schemas.execution_analysis import AnalysisRunPage
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.execution.test_postgres_execution_view import write
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
    analysis_repository,
)

pytestmark = pytest.mark.asyncio


async def test_zero_one_seven_historical_utc_run_pages(datasets):
    service, scope, principal, *_ = datasets
    repository = analysis_repository(service)
    start = datetime(2026, 9, 23, tzinfo=UTC)
    identities = [uuid4() for _ in range(7)]
    for offset, run in enumerate(identities):
        at = start + timedelta(days=offset, hours=12)
        await write(
            run,
            scope,
            1,
            {
                "family": "agent",
                "status": "completed",
                "admitted_at": at.isoformat(),
                "terminal_at": (at + timedelta(seconds=1)).isoformat(),
            },
        )
    for count in (0, 1, 7):
        query = AnalysisQuery.parse(
            {
                "start": (start - timedelta(days=count == 0)).isoformat(),
                "end": (start + timedelta(days=count)).isoformat(),
            },
            "day",
            "UTC",
        )
        capture = await repository.capture(scope, principal, query, None)
        replay = await repository.capture(scope, principal, query, capture.watermark)
        page = await repository.run_page(scope, principal, replay)
        public = AnalysisRunPage.model_validate(page)
        assert public.availability == "available"
        assert {row.run_id for row in public.items} == {str(run) for run in identities[:count]}
        assert len({row["group"]["bucket"] for row in capture.metrics["series"]}) == count
