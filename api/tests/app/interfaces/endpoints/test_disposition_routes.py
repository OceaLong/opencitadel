"""Server-authored destructive previews are mandatory and freshness-bound."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.run_routes import router
from app.interfaces.service_dependencies import get_kernel_api_runtime
from app.kernel.application.ports import CommandResult, CommandResultStatus

RUN_ID = UUID(int=10301)


class Commands:
    async def submit(self, command, authorization):
        return CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.SUCCEEDED,
            stream_version=4,
        )


class Queries:
    async def get_run(self, run_id, scope):
        return {"id": str(run_id), "workflow": "agent", "status": "idle"}


class Dispositions:
    async def preview_run(self, run_id, scope, *, action):
        return {
            "action": action,
            "recoverable": action == "archive",
            "affectedCounts": {"messages": 2},
            "purgeAfter": "2026-10-02T08:00:00+00:00",
            "confirmation": f"{action.upper()} RUN {run_id}",
            "planHash": "f" * 64,
        }

    async def validate_run(self, run_id, scope, *, action, plan_hash, confirmation):
        return (
            None if plan_hash == "s" * 64 else await self.preview_run(run_id, scope, action=action)
        )


def _client():
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_kernel_api_runtime] = lambda: SimpleNamespace(
        commands=Commands(), queries=Queries(), dispositions=Dispositions()
    )
    application.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
    )
    return TestClient(application)


def test_archive_rejects_a_stale_server_plan() -> None:
    client = _client()
    preview = client.get(f"/api/runs/{RUN_ID}/disposition?action=archive")
    assert preview.status_code == 200
    assert preview.json()["data"]["recoverable"] is True

    stale = client.post(
        f"/api/runs/{RUN_ID}/commands/archive",
        json={
            "planHash": "s" * 64,
            "confirmation": f"ARCHIVE RUN {RUN_ID}",
        },
    )
    assert stale.status_code == 409


def test_fresh_archive_plan_becomes_a_typed_command() -> None:
    response = _client().post(
        f"/api/runs/{RUN_ID}/commands/archive",
        json={
            "planHash": "f" * 64,
            "confirmation": f"ARCHIVE RUN {RUN_ID}",
        },
    )
    assert response.status_code == 202
