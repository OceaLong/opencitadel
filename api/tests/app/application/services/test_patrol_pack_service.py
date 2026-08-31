from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_pack_service import PatrolPackService
from app.domain.errors import ConflictError
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)

READ = ToolExecutionPolicy(
    capability=ToolCapability.INTEGRATION_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)


class Repo:
    def __init__(self):
        self.pack = None

    async def save_pack(self, pack):
        self.pack = pack
        return pack

    async def get_pack(self, pack_id, scope=None, for_update=False):
        return (
            self.pack
            if self.pack and self.pack.id == pack_id and self.pack.owner_user_id == scope.user_id
            else None
        )

    async def list_packs(self, scope, **kwargs):
        return (
            [self.pack]
            if self.pack and self.pack.owner_user_id == scope.user_id and not self.pack.deleted_at
            else []
        )


class JobRepo:
    def __init__(self):
        self.jobs = {}

    async def save(self, job):
        self.jobs[job.id] = job

    async def get_by_id(self, job_id, scope=None):
        return self.jobs.get(job_id)


class Uow:
    def __init__(self, patrol, server, jobs):
        self.patrol = patrol
        self.mcp_server = SimpleNamespace(get_by_id=AsyncMock(return_value=server))
        self.scheduled_job = jobs
        self.execution_commands = SimpleNamespace()

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_pack_lifecycle_versions_validation_and_schedule():
    config = load_patrol_template("kubernetes-baseline-v1")
    capabilities = {
        "enabled_tools": [
            "get_capabilities",
            *sorted({check.probe.tool for check in config.checks}),
        ],
        "output_schema_hashes": {
            check.probe.tool: check.probe.output_schema_hash for check in config.checks
        },
        "overall_capability_hash": "c" * 64,
    }
    policies = dict.fromkeys(capabilities["enabled_tools"], READ)
    server = MCPServerRecord(
        id="server-1",
        name="collector",
        url="https://collector.example/mcp",
        tool_policies=policies,
        transport_options={"patrol_capabilities": capabilities},
    )
    patrol, jobs = Repo(), JobRepo()
    uow = Uow(patrol, server, jobs)
    admission = SimpleNamespace(
        admit=AsyncMock(return_value=UUID("70000000-0000-0000-0000-000000000001"))
    )
    service = PatrolPackService(lambda: uow, run_admission_service=admission)
    scope = OwnerScope.personal("user-1")

    pack = await service.create_pack(
        owner_user_id="user-1",
        scope=scope,
        name="Daily",
        mcp_server_id="server-1",
        config=config,
    )
    assert pack.version == 1
    assert pack.status.value == "draft"
    assert jobs.jobs[pack.scheduled_job_id].source_id == pack.id
    validating = await service.request_validation(
        pack.id,
        scope,
        "user-1",
    )
    assert validating.status.value == "validating"
    assert validating.validation_run_id is not None
    UUID(validating.validation_run_id)
    admission.admit.assert_awaited_once()
    assert admission.admit.await_args.kwargs["family"].value == "patrol"
    assert admission.admit.await_args.kwargs["workflow"] == {
        "operation": "validate",
        "pack_id": pack.id,
        "pack_version": 1,
        "validation_run_id": validating.validation_run_id,
    }
    assert admission.admit.await_args.kwargs["run_id"] == UUID(validating.validation_run_id)
    assert admission.admit.await_args.kwargs["command_sink"] is uow.execution_commands

    validated = await service.complete_validation(
        pack_id=pack.id,
        scope=scope,
        actor_user_id="user-1",
        validation_run_id=validating.validation_run_id,
        validated_version=1,
        capabilities=capabilities,
        dry_run={"ok": True, "mode": "live-read-only-preflight", "probes": []},
        errors=[],
    )
    assert validated.last_validated_version == 1
    assert validated.validation_run_id is None
    active = await service.activate_pack(pack.id, scope, "user-1")
    assert active.status.value == "active"
    patched = await service.patch_pack(
        pack.id, scope, "user-1", expected_version=1, name="Daily v2"
    )
    assert patched.version == 2
    assert patched.last_validated_version is None
    with pytest.raises(ConflictError):
        await service.activate_pack(pack.id, scope, "user-1")


@pytest.mark.asyncio
async def test_validation_rejects_non_read_only_policy():
    config = load_patrol_template("kubernetes-baseline-v1")
    server = MCPServerRecord(
        id="server-1",
        name="collector",
        url="https://collector.example/mcp",
        tool_policies={"get_capabilities": READ},
        transport_options={"patrol_capabilities": {}},
    )
    patrol, jobs = Repo(), JobRepo()
    admission = SimpleNamespace(admit=AsyncMock())
    service = PatrolPackService(
        lambda: Uow(patrol, server, jobs),
        run_admission_service=admission,
    )
    pack = await service.create_pack(
        owner_user_id="user-1",
        scope=OwnerScope.personal("user-1"),
        name="Daily",
        mcp_server_id="server-1",
        config=config,
    )
    invalid = await service.request_validation(
        pack.id,
        OwnerScope.personal("user-1"),
        "user-1",
    )
    assert invalid.status.value == "invalid"
    assert any("policy missing" in item for item in invalid.validation_summary["errors"])
    admission.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_completion_rejects_stale_run_identity():
    config = load_patrol_template("kubernetes-baseline-v1")
    tools = ["get_capabilities", *sorted({check.probe.tool for check in config.checks})]
    server = MCPServerRecord(
        id="server-1",
        name="collector",
        url="https://collector.example/mcp",
        tool_policies=dict.fromkeys(tools, READ),
    )
    patrol, jobs = Repo(), JobRepo()
    admission = SimpleNamespace(
        admit=AsyncMock(return_value=UUID("70000000-0000-0000-0000-000000000002"))
    )
    service = PatrolPackService(
        lambda: Uow(patrol, server, jobs),
        run_admission_service=admission,
    )
    scope = OwnerScope.personal("user-1")
    pack = await service.create_pack(
        owner_user_id="user-1",
        scope=scope,
        name="Daily",
        mcp_server_id="server-1",
        config=config,
    )
    await service.request_validation(pack.id, scope, "user-1")

    with pytest.raises(ConflictError):
        await service.complete_validation(
            pack_id=pack.id,
            scope=scope,
            actor_user_id="user-1",
            validation_run_id="70000000-0000-0000-0000-000000000099",
            validated_version=1,
            capabilities={},
            dry_run={},
            errors=["stale"],
        )
