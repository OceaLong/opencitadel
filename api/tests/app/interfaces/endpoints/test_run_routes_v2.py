"""HTTP contract for Run commands and projection reads."""

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

RUN_ID = UUID(int=10101)


class Commands:
    def __init__(self) -> None:
        self.values = []

    async def submit(self, command, authorization):
        self.values.append((command, authorization))
        return CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.SUCCEEDED,
            stream_version=3,
        )


class Queries:
    async def list_runs(self, scope, *, status=None, limit=50):
        return [{"id": str(RUN_ID), "workflow": "agent", "status": "idle"}]

    async def get_run(self, run_id, scope):
        return {"id": str(run_id), "workflow": "agent", "status": "idle"}

    async def history(self, run_id, scope, *, after_version=0):
        return [{"version": 1, "type": "RunStarted"}]


class Dispositions:
    pass


class Catalog:
    async def for_scope(self, scope):
        return [
            {
                "name": "browser.view",
                "safety": "read_only",
                "requires_approval": False,
                "effect_type": "tool.call",
            }
        ]


def _client():
    commands = Commands()
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_kernel_api_runtime] = lambda: SimpleNamespace(
        commands=commands,
        queries=Queries(),
        dispositions=Dispositions(),
        catalog=Catalog(),
    )
    application.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
    )
    return TestClient(application), commands


def test_create_prompt_cancel_and_reads_use_typed_commands() -> None:
    client, commands = _client()
    created = client.post(
        "/api/runs",
        json={"prompt": "inspect", "title": "Audit", "knowledgeVersionIds": []},
    )
    prompted = client.post(
        f"/api/runs/{RUN_ID}/commands/prompt",
        json={"prompt": "continue", "expectedStreamVersion": 3},
    )
    cancelled = client.post(
        f"/api/runs/{RUN_ID}/commands/cancel",
        json={"reason": "user_requested"},
    )

    assert [response.status_code for response in (created, prompted, cancelled)] == [
        202,
        202,
        202,
    ]
    assert [value[0].type for value in commands.values] == [
        "StartAgent",
        "SubmitPrompt",
        "CancelRun",
    ]
    assert commands.values[0][0].payload["tool_catalog"][0]["name"] == "browser.view"
    assert client.get("/api/runs").json()["data"][0]["status"] == "idle"
    assert client.get(f"/api/runs/{RUN_ID}").json()["data"]["workflow"] == "agent"
    assert client.get(f"/api/runs/{RUN_ID}/history").json()["data"][0]["version"] == 1


def test_run_writes_do_not_accept_arbitrary_status() -> None:
    client, commands = _client()
    response = client.post("/api/runs", json={"prompt": "x", "status": "completed"})
    assert response.status_code == 422
    assert commands.values == []
