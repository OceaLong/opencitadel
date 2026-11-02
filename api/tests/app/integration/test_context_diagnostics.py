# ruff: noqa: F401,F811 -- real PostgreSQL fixture dependencies
"""Context diagnostics survive the formal journal, fixed cuts and actual API RLS."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import WorkspaceContext
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_view_routes import router
from app.interfaces.service_dependencies import get_execution_view_service
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize(
    "compact_runtime",
    [{"ordinary": True, "ready": True, "real_preparation": True, "iterations": 1}],
    indirect=True,
)
async def test_context_diagnostics_are_visible_at_their_formal_cut(compact_runtime):
    runtime = compact_runtime
    views = ExecutionViewService(
        PostgresExecutionView(
            session_factory=runtime.api_uow().session_factory,
            authorization=AuthorizationContext.for_principal(
                runtime.principal, scope=runtime.scope
            ),
        ),
        cursor_secret=b"owned-context-view",
    )
    pending = None
    for _ in range(30):
        await runtime.kernel.run_projector_once(runtime.scope, limit=100)
        decisions = await runtime.kernel.run_decisions_once(limit=1)
        assert not decisions.errors
        assert not decisions.rejected
        if pending is None:
            await runtime.kernel.run_projector_once(runtime.scope, limit=100)
            page = await views.get_view(runtime.scope, runtime.binding.run_id)
            if any(step.context is not None for step in page.steps):
                pending = page
        activities = await runtime.kernel.run_activities_once(limit=1)
        assert not activities.failed
        assert not activities.stale
        if not decisions.submitted and not activities.claimed:
            break
    assert pending is not None
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=runtime.principal, scope=runtime.scope
    )
    app.dependency_overrides[get_execution_view_service] = lambda: views
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://owned-test"
    ) as client:
        path = f"/execution-runs/{runtime.binding.run_id}/contexts"
        response = await client.get(path)
        assert response.status_code == 200, response.text
        latest = response.json()["data"]
        assert len(latest["items"]) == 2
        assert all(item["context"]["state"] == "ready" for item in latest["items"])
        assert all(item["context"]["definition_version"] == 1 for item in latest["items"])
        first = latest["items"][0]
        detail = await client.get(path + "/" + first["step_id"], params={"at": latest["at"]})
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["context"] == first["context"]
        old = await client.get(path, params={"at": pending.at})
        assert old.status_code == 200, old.text
        assert len(old.json()["data"]["items"]) == 1
        assert old.json()["data"]["items"][0]["context"]["state"] is None
        assert "retain exact facts" not in response.text
        assert "execution/" not in response.text
