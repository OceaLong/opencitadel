"""Owned PostgreSQL capture immutability and privilege gates; collect only locally."""

# ruff: noqa: F401,F811
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.ports.execution_analysis import AnalysisQuery
from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.execution.test_postgres_execution_view import write
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
    analysis_repository,
)
from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
    capture,
    make_run,
)

pytestmark = pytest.mark.asyncio


async def test_new_comparison_freezes_tool_identity_error_counts_and_latency(datasets):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    activity_id = str(uuid4())
    await write(
        UUID(run_id),
        scope,
        2,
        {
            "kind": "tool",
            "activity_id": activity_id,
            "attempt_id": "attempt",
            "status": "failed",
            "tool_name": "original",
            "business_outcome": "failed",
        },
        kind="step",
        identity="tool-step",
    )
    repo, identity, revision = await capture(service, scope, principal, [run_id])
    before = (await repo.read(scope, principal, identity, revision)).body["metrics"]["charts"]
    assert before["tools"]["items"][0]["tool_name"] == "original"
    assert before["tools"]["items"][0]["errors"] == 1
    await write(
        UUID(run_id),
        scope,
        3,
        {
            "kind": "tool",
            "activity_id": activity_id,
            "attempt_id": "attempt",
            "status": "completed",
            "tool_name": "latest",
            "business_outcome": "success",
        },
        kind="step",
        identity="tool-step",
    )
    after = (await repo.read(scope, principal, identity, revision)).body["metrics"]["charts"]
    assert after == before


async def test_live_sealed_chart_watermark_replays_exact_original_facts(datasets):
    service, scope, principal, *_ = datasets
    await make_run(scope)
    repo = analysis_repository(service)
    query = AnalysisQuery.parse({}, "day", "UTC")
    original = await repo.capture(scope, principal, query, None)
    await make_run(scope)
    replay = await repo.capture(scope, principal, query, original.watermark)
    assert replay.metrics["charts"] == original.metrics["charts"]


async def test_chart_private_facts_have_no_runtime_raw_access(datasets):
    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        for table in ("comparison_tool_facts", "comparison_tool_captures"):
            assert await work.db_session.scalar(
                text(
                    "SELECT NOT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                ),
                {"table": table},
            )
        assert not await work.db_session.scalar(
            text(
                "SELECT has_function_privilege(current_user,'opencitadel_analysis_tool_rows(text,uuid[])','EXECUTE')"
            )
        )


async def test_zero_member_new_comparison_has_observed_empty_tool_capture(datasets):
    service, scope, principal, *_ = datasets
    repo, identity, revision = await capture(service, scope, principal)
    result = (await repo.read(scope, principal, identity, revision)).body["metrics"]["charts"]
    assert result["tools"] == {"availability": "available", "items": []}
    assert result["latency"]["p50"]["value"] is None
