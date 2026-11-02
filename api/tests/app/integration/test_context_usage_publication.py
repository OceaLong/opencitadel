# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Main/summary costs use the existing receipt publication and immutable view cut."""

import pytest

from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)
from tests.app.integration.test_context_fixed_collection import HISTORY

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "provided_history": HISTORY,
        }
    ],
    indirect=True,
)
async def test_context_cost_groups_keep_unknown_dispatch_phase_and_original_cut(compact_runtime):
    runtime = compact_runtime
    for _ in range(60):
        await runtime.kernel.run_projector_once(runtime.scope, limit=100)
        decisions = await runtime.kernel.run_decisions_once(limit=1)
        activities = await runtime.kernel.run_activities_once(limit=1)
        assert not decisions.errors
        assert not activities.failed
        if decisions.submitted == 0 and activities.claimed == 0:
            break
    assert runtime.responses.summary
    assert len(runtime.responses.main) == 2
    views = ExecutionViewService(
        PostgresExecutionView(
            session_factory=runtime.api_uow().session_factory,
            authorization=AuthorizationContext.for_principal(
                runtime.principal, scope=runtime.scope
            ),
        ),
        cursor_secret=b"owned-usage-group-view",
    )
    maintenance = ExecutionUsageMaintenance(
        session_factory=runtime.sessions,
        authorization=runtime.auth,
        handler=runtime.kernel,
    )
    assert (await maintenance.process_pending(limit=100))["emitted"] == len(runtime.sends)
    await runtime.kernel.run_projector_once(runtime.scope, limit=100)
    dispatched = await views.get_view(runtime.scope, runtime.binding.run_id)
    initial = dispatched.run.usage["production"]
    assert initial.calls == len(runtime.sends)
    assert initial.groups["main_answer"].calls == 2
    assert initial.groups["context_summary"].calls == len(runtime.responses.summary)
    assert initial.groups["context_summary"].unknown_usage_calls == len(runtime.responses.summary)
    assert initial.groups["context_summary"].unknown_cost_calls == len(runtime.responses.summary)
    assert (await maintenance.process_pending(limit=100))["emitted"] == len(runtime.sends)
    await runtime.kernel.run_projector_once(runtime.scope, limit=100)
    settled = await views.get_view(runtime.scope, runtime.binding.run_id)
    assert settled.run.usage["production"].unknown_usage_calls == 0
    assert settled.run.usage["production"].calls == initial.calls
    old = await views.get_view(runtime.scope, runtime.binding.run_id, at=dispatched.at)
    assert old.run.usage == dispatched.run.usage
