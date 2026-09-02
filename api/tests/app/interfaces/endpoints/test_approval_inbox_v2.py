"""HTTP approval-center contract."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.approval_routes import router
from app.interfaces.service_dependencies import get_kernel_api_runtime
from app.kernel.application.ports import CommandResult, CommandResultStatus

APPROVAL_ID = UUID(int=10201)
RUN_ID = UUID(int=10202)


class Commands:
    def __init__(self) -> None:
        self.command = None

    async def submit(self, command, authorization):
        self.command = command
        return CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.SUCCEEDED,
            stream_version=9,
        )


class Queries:
    async def list_approvals(self, actor_user_id, *, status=None, team_id=None, limit=50):
        return [
            {
                "id": str(APPROVAL_ID),
                "runId": str(RUN_ID),
                "subject": "shell.run",
                "status": "pending",
            }
        ]

    async def approval_context(self, approval_id, actor_user_id):
        return {
            "run_id": RUN_ID,
            "workflow": "agent",
            "owner_user_id": "user-1",
            "team_id": None,
        }


def test_approval_center_lists_and_submits_feedback_decision() -> None:
    commands = Commands()
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_kernel_api_runtime] = lambda: SimpleNamespace(
        commands=commands,
        queries=Queries(),
    )
    application.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
    )
    client = TestClient(application)

    listed = client.get("/api/approvals?status=pending")
    decided = client.post(
        f"/api/approvals/{APPROVAL_ID}/commands/decide",
        json={"decision": "approved", "feedback": "one invocation only"},
    )

    assert listed.status_code == 200
    assert listed.json()["data"][0]["subject"] == "shell.run"
    assert decided.status_code == 202
    assert commands.command.type == "DecideApproval"
    assert commands.command.payload["feedback"] == "one invocation only"
