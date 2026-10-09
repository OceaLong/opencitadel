# ruff: noqa: F811
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_actual_http_registration_provenance_admin_and_secret_free_response(datasets):
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment import TestTarget
    from app.domain.models.scope import Principal, WorkspaceContext
    from app.domain.models.user import GlobalRole
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_environment_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_environment_service
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    target = TestTarget(
        id=uuid4(), kind="http", physical_resource="owned", endpoint="http://allowed.e04.test:8081"
    )
    service = EnvironmentService(ds.uow_factory, AdapterRegistry(targets=(target,)))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_environment_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        payload = {
            "request_id": str(uuid4()),
            "kind": "target",
            "value": target.model_dump(mode="json"),
        }
        denied = await client.post("/evaluation/environments/registry", json=payload)
        assert denied.status_code == 403
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
            )
            await db.commit()
        principal = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
        registered = await client.post("/evaluation/environments/registry", json=payload)
        assert registered.status_code == 200, registered.text
        assert registered.json()["data"] == {"id": str(target.id), "kind": "target", "revision": 1}
        assert registered.headers["cache-control"] == "no-store"
        assert "endpoint" not in registered.text
        assert "locator" not in registered.text
        assert "contracts" not in registered.text
        malicious = dict(payload, request_id=str(uuid4()), is_test=True)
        assert (
            await client.post("/evaluation/environments/registry", json=malicious)
        ).status_code == 400
        malicious = dict(
            payload,
            request_id=str(uuid4()),
            value=dict(payload["value"], endpoint="https://production.example"),
        )
        assert (
            await client.post("/evaluation/environments/registry", json=malicious)
        ).status_code in {400, 422}

        mismatched = dict(payload, kind="credential", request_id=str(uuid4()))
        assert (
            await client.post("/evaluation/environments/registry", json=mismatched)
        ).status_code == 400
