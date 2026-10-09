"""Cache release barriers exercised independently of the PostgreSQL transport."""

from datetime import UTC, datetime

import pytest

from app.domain.models.scope import OwnerScope, Principal


@pytest.mark.asyncio
async def test_revocation_after_compute_never_releases_old_count():
    from app.application.ports.execution_analysis import (
        AnalysisCapture,
        AuthorityState,
        CoverageChanged,
    )
    from app.application.services.execution_analysis_service import ExecutionAnalysisService

    class Repository:
        revision = 1

        async def capture(self, scope, principal, query, watermark):
            result = AnalysisCapture(
                "fixed-cut", AuthorityState(1, "manifest-a"), {"run_count": 99}
            )
            self.revision = 2
            return result

        async def current(self, scope, principal, capture):
            return AuthorityState(self.revision, "manifest-a")

    service = ExecutionAnalysisService(Repository(), Principal(user_id="a"))
    with pytest.raises(CoverageChanged):
        await service.summary(OwnerScope.personal("a"), {}, "day", "UTC", None)


@pytest.mark.asyncio
async def test_resource_transfer_invalidates_cache_even_with_same_epoch():
    from app.application.ports.execution_analysis import (
        AnalysisCapture,
        AuthorityState,
        CoverageChanged,
    )
    from app.application.services.execution_analysis_service import ExecutionAnalysisService

    class Repository:
        manifest = "original"

        async def capture(self, scope, principal, query, watermark):
            return AnalysisCapture("cut", AuthorityState(7, "original"), {"run_count": 5})

        async def current(self, scope, principal, capture):
            return AuthorityState(7, self.manifest)

    repo = Repository()
    service = ExecutionAnalysisService(repo, Principal(user_id="a"))
    scope = OwnerScope.personal("a")
    first = await service.summary(scope, {}, "day", "UTC", None)
    assert first.metrics["run_count"] == 5
    repo.manifest = "transferred"
    with pytest.raises(CoverageChanged):
        await service.summary(scope, {}, "day", "UTC", first.watermark)


@pytest.mark.asyncio
async def test_cache_expiry_recomputes_without_relabelling_fixed_cut():
    from app.application.ports.execution_analysis import AnalysisCapture, AuthorityState
    from app.application.services.execution_analysis_service import ExecutionAnalysisService

    class Repository:
        calls = 0

        async def capture(self, scope, principal, query, watermark):
            self.calls += 1
            return AnalysisCapture(
                watermark or str(self.calls), AuthorityState(1, "m"), {"calls": self.calls}
            )

        async def current(self, scope, principal, capture):
            return capture.authority

    clock = [0]
    repo = Repository()
    service = ExecutionAnalysisService(repo, Principal(user_id="a"), clock=lambda: clock[0])
    args = (OwnerScope.personal("a"), {}, "day", "UTC", None)
    first = await service.summary(*args)
    clock[0] = 29
    assert (await service.summary(*args)).watermark == first.watermark
    clock[0] = 31
    assert (await service.summary(*args)).watermark != first.watermark


def test_analysis_query_half_open_bounded_and_timezone_precedence():
    from app.application.ports.execution_analysis import AnalysisQuery

    now = datetime(2026, 3, 9, tzinfo=UTC)
    query = AnalysisQuery.parse(
        {}, "day", "Asia/Shanghai", workspace_timezone="America/New_York", now=now
    )
    assert query.timezone == "America/New_York"
    assert (query.end - query.start).days == 7
    with pytest.raises(ValueError, match="analysis_range_exceeded"):
        AnalysisQuery.parse({"start": "2020-01-01T00:00:00Z"}, "day", "UTC", now=now)
    with pytest.raises(ValueError, match="invalid_analysis_query"):
        AnalysisQuery.parse({"unexpected": "x"}, "day", "UTC", now=now)


@pytest.mark.asyncio
async def test_mutating_response_cannot_poison_other_cached_responses():
    from app.application.ports.execution_analysis import AnalysisCapture, AuthorityState
    from app.application.services.execution_analysis_service import ExecutionAnalysisService

    class Repository:
        async def capture(self, scope, principal, query, watermark):
            return AnalysisCapture("cut", AuthorityState(1, "m"), {"nested": {"value": 5}})

        async def current(self, scope, principal, capture):
            return capture.authority

    service = ExecutionAnalysisService(Repository(), Principal(user_id="a"))
    args = (OwnerScope.personal("a"), {}, "day", "UTC", None)
    result = await service.summary(*args)
    result.metrics["nested"]["value"] = 999
    assert (await service.summary(*args)).metrics["nested"]["value"] == 5


def test_capture_replay_uses_saved_default_range_but_rejects_changed_explicit_range():
    from app.infrastructure.repositories.db_execution_analysis_repository import same_query

    saved = {
        "start": "2026-01-01",
        "end": "2026-01-08",
        "start_explicit": False,
        "end_explicit": False,
        "filters": [],
    }
    later = {**saved, "start": "2026-01-02", "end": "2026-01-09"}
    assert same_query(saved, later)
    assert not same_query({**saved, "end_explicit": True}, {**later, "end_explicit": True})


@pytest.mark.asyncio
async def test_supervised_cleanup_includes_expired_analysis_captures():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.infrastructure.evaluation.runtime_inventory import EvaluationRuntimeInventory

    committed = []

    class Summary:
        async def cleanup_expired(self, *, limit):
            return 1

    class Database:
        async def scalar(self, statement, parameters):
            assert parameters["limit"] == 100
            return 2

    async def commit():
        committed.append(True)

    @asynccontextmanager
    async def factory(context):
        assert context.system_actor == "execution-kernel"
        yield SimpleNamespace(evaluation_summary=Summary(), db_session=Database(), commit=commit)

    assert await EvaluationRuntimeInventory(factory).cleanup_summary() == 3
    assert committed == [True]


def test_analysis_certification_openapi_is_bounded_and_has_typed_safe_response():
    from fastapi import FastAPI

    from app.interfaces.endpoints.evaluation_dataset_routes import router

    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    operation = schema["paths"]["/evaluation/dataset-versions/{version_id}/analysis-certification"][
        "post"
    ]
    assert "requestBody" not in operation
    version = next(p for p in operation["parameters"] if p["name"] == "version_id")
    assert version["schema"]["format"] == "uuid"
    result = schema["components"]["schemas"]["AnalysisCertification"]
    assert set(result["properties"]) == {"status"}
    assert result["properties"]["status"]["const"] == "certified"


async def test_production_factory_keeps_bounded_cache_but_rechecks_preference_and_authority():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.ports.execution_analysis import AnalysisCapture, AuthorityState
    from app.composition.execution_analysis import AnalysisServiceFactory

    principal = Principal(user_id="u")
    scope = OwnerScope.personal("u")
    port = SimpleNamespace(
        capture=AsyncMock(return_value=AnalysisCapture("fixed", AuthorityState(1, "m"), {})),
        current=AsyncMock(return_value=AuthorityState(1, "m")),
    )
    preferences = SimpleNamespace(get=AsyncMock(return_value={"timezone": None, "revision": 0}))
    factory = AnalysisServiceFactory(port, preferences, max_callers=1)
    first = await factory(scope, principal)
    await first.summary(scope, {}, "day", "UTC")
    second = await factory(scope, principal)
    assert first is second
    await second.summary(scope, {}, "day", "UTC")
    assert port.capture.await_count == 1
    assert port.current.await_count == 2
    assert preferences.get.await_count == 2
    await factory(OwnerScope.personal("v"), Principal(user_id="v"))
    assert await factory(scope, principal) is not first
