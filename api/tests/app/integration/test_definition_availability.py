# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Worker TTL diagnostics are live, authorized and independent of formal state."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update

from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import WorkspaceContext
from app.infrastructure.execution.definition_models import workers
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_view_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_execution_view_service
from tests.app.infrastructure.execution.test_definition_lifecycle import capability
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
async def test_v02_v03_live_availability_expires_and_recovers_without_run_mutations(
    compact_runtime,
):
    runtime = compact_runtime
    await runtime.kernel.run_projector_once(runtime.scope, limit=100)
    run_id = runtime.binding.run_id
    async with runtime.uow(runtime.auth) as work:
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                run_id, owner_scope=(runtime.scope.user_id, None)
            )
        ).replay.state
        definition = state.definition_manifest
        await PostgresDefinitionStore(work.db_session).register(definition)
        await work.commit()
    auth = AuthorizationContext.for_principal(runtime.principal, scope=runtime.scope)
    views = ExecutionViewService(
        PostgresExecutionView(
            session_factory=runtime.api_uow().session_factory, authorization=auth
        ),
        cursor_secret=b"owned-availability-view",
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=runtime.principal, scope=runtime.scope
    )
    app.dependency_overrides[get_execution_view_service] = lambda: views
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://owned-test"
    ) as client:
        path = f"/execution-runs/{run_id}/definition-availability"
        unavailable = await client.get(path)
        assert unavailable.status_code == 200, unavailable.text
        assert unavailable.headers["cache-control"] == "no-store"
        original = unavailable.json()["data"]
        assert original["state"] == "version_unavailable"
        assert original["reason"] == "VERSION_UNAVAILABLE"
        assert original["definition_digest"] == definition.digest
        assert not {"worker_id", "body", "build_artifact_digest"} & original.keys()
        async with runtime.uow(runtime.auth) as work:
            await PostgresDefinitionStore(work.db_session).heartbeat(
                capability(definition), ttl=timedelta(seconds=30)
            )
            await work.commit()
        available = (await client.get(path)).json()["data"]
        assert available["state"] == "available"
        assert available["reason"] is None
        assert available["checked_at"] >= original["checked_at"]
        async with runtime.uow(runtime.auth) as work:
            await work.db_session.execute(
                update(workers).values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
            await work.commit()
        assert (await client.get(path)).json()["data"]["state"] == "version_unavailable"
        async with runtime.uow(runtime.auth) as work:
            await PostgresDefinitionStore(work.db_session).heartbeat(
                capability(definition), ttl=timedelta(seconds=30)
            )
            await work.commit()
        assert (await client.get(path)).json()["data"]["state"] == "available"
        # A guessed Run cannot enumerate a definition or worker availability.
        assert (
            await client.get(f"/execution-runs/{uuid4()}/definition-availability")
        ).status_code == 404
    async with runtime.api_uow() as work:
        assert list((await work.db_session.execute(select(workers))).all()) == []
        assert (
            list(
                (
                    await work.db_session.execute(
                        text(
                            "SELECT * FROM public.opencitadel_definition_availability(:scope,:run)"
                        ),
                        {"scope": "user:another-owner", "run": run_id},
                    )
                ).all()
            )
            == []
        )
    async with runtime.uow(runtime.auth) as work:
        row = await work.db_session.get(ExecutionRunProjectionORM, run_id)
        assert row.stream_version == state.stream_version
        assert row.status == state.status.value
        assert row.decision_due_at is not None
        assert (
            await work.db_session.scalar(select(func.count()).select_from(ExecutionActivityTaskORM))
            == 0
        )
