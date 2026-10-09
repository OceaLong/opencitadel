# ruff: noqa: F811
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_async_recording_http_create_status_and_no_early_result(datasets):
    from app.application.evaluation.recording_service import RecordingService
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_recording_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_recording_service

    ds, scope, principal, _, _ = datasets

    class Views:
        async def get_view(self, *args):
            return object()

    service = RecordingService(ds.uow_factory, source=SimpleNamespace(views=Views()))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_recording_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/evaluation/recordings",
            json={
                "run_id": str(uuid4()),
                "request_id": str(uuid4()),
                "selections": [{"tool": "read", "allowed_fields": ["success"]}],
            },
        )
        assert result.status_code == 202, result.text
        assert result.json()["code"] == 202
        value = result.json()["data"]
        assert value["status"] == "queued"
        assert value["result_version"] is None
        status = await client.get("/evaluation/recordings/" + value["id"])
        assert status.status_code == 200
        assert status.headers["cache-control"] == "no-store"
        result = await client.get("/evaluation/recordings/" + value["id"] + "/result")
        assert result.status_code == 409
        for field in ["storage_key", "principal", "selection", "connector_bindings"]:
            assert field not in status.text
