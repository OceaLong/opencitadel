from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.errors import ConflictError
from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_pack_service import PatrolPackService
from app.domain.models.app_config import AppConfig
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.scope import OwnerScope
from app.domain.models.skill import Skill
from app.domain.models.tool_policy import ApprovalMode, ToolCapability, ToolEffect, ToolExecutionPolicy, ToolIdempotency


READ = ToolExecutionPolicy(capability=ToolCapability.INTEGRATION_READ, effect=ToolEffect.READ_ONLY, idempotency=ToolIdempotency.SAFE, approval=ApprovalMode.NEVER)


class Repo:
    def __init__(self):
        self.pack = None

    async def save_pack(self, pack):
        self.pack = pack
        return pack

    async def get_pack(self, pack_id, scope=None, for_update=False):
        return self.pack if self.pack and self.pack.id == pack_id and self.pack.owner_user_id == scope.user_id else None

    async def list_packs(self, scope, **kwargs):
        return [self.pack] if self.pack and self.pack.owner_user_id == scope.user_id and not self.pack.deleted_at else []


class JobRepo:
    def __init__(self): self.jobs = {}
    async def save(self, job): self.jobs[job.id] = job
    async def get_by_id(self, job_id, scope=None): return self.jobs.get(job_id)


class Uow:
    def __init__(self, patrol, server, skill, jobs):
        self.patrol = patrol
        self.mcp_server = SimpleNamespace(get_by_id=AsyncMock(return_value=server))
        self.skill = SimpleNamespace(get_by_slug=AsyncMock(return_value=skill), get_by_id=AsyncMock(return_value=skill))
        self.scheduled_job = jobs
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None


@pytest.fixture
def enabled_config():
    config = AppConfig()
    config.feature_flags.enable_ops_patrol = True
    return config


@pytest.mark.asyncio
async def test_pack_lifecycle_versions_validation_and_schedule(enabled_config):
    config = load_patrol_template("kubernetes-baseline-v1")
    capabilities = {
        "enabled_tools": ["get_capabilities", *sorted({check.probe.tool for check in config.checks})],
        "output_schema_hashes": {check.probe.tool: check.probe.output_schema_hash for check in config.checks},
        "overall_capability_hash": "c" * 64,
    }
    policies = {name: READ for name in capabilities["enabled_tools"]}
    server = MCPServerRecord(id="server-1", name="collector", url="https://collector.example/mcp", tool_policies=policies, extra={"patrol_capabilities": capabilities})
    skill = Skill(id="skill-1", name="Patrol", slug="ops-patrol")
    patrol, jobs = Repo(), JobRepo()
    uow = Uow(patrol, server, skill, jobs)
    service = PatrolPackService(lambda: uow)
    scope = OwnerScope.personal("user-1")

    with patch("app.application.services.patrol_pack_service.get_runtime_config", return_value=enabled_config):
        pack = await service.create_pack(owner_user_id="user-1", scope=scope, name="Daily", mcp_server_id="server-1", config=config)
        assert pack.version == 1 and pack.status.value == "draft"
        assert jobs.jobs[pack.scheduled_job_id].source_id == pack.id
        validated = await service.validate_pack(pack.id, scope, "user-1")
        assert validated.last_validated_version == 1
        active = await service.activate_pack(pack.id, scope, "user-1")
        assert active.status.value == "active"
        patched = await service.patch_pack(pack.id, scope, "user-1", expected_version=1, name="Daily v2")
        assert patched.version == 2 and patched.last_validated_version is None
        with pytest.raises(ConflictError):
            await service.activate_pack(pack.id, scope, "user-1")


@pytest.mark.asyncio
async def test_validation_rejects_non_read_only_policy(enabled_config):
    config = load_patrol_template("kubernetes-baseline-v1")
    server = MCPServerRecord(id="server-1", name="collector", url="https://collector.example/mcp", tool_policies={"get_capabilities": READ}, extra={"patrol_capabilities": {}})
    skill = Skill(id="skill-1", name="Patrol", slug="ops-patrol")
    patrol, jobs = Repo(), JobRepo()
    service = PatrolPackService(lambda: Uow(patrol, server, skill, jobs))
    with patch("app.application.services.patrol_pack_service.get_runtime_config", return_value=enabled_config):
        pack = await service.create_pack(owner_user_id="user-1", scope=OwnerScope.personal("user-1"), name="Daily", mcp_server_id="server-1", config=config)
        invalid = await service.validate_pack(pack.id, OwnerScope.personal("user-1"), "user-1")
    assert invalid.status.value == "invalid"
    assert any("policy missing" in item for item in invalid.validation_summary["errors"])
