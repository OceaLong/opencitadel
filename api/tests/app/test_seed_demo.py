"""Idempotency + wiring tests for api/app/seed_demo.py (Phase B demo loop, Task 2).

All service classes here are the real application services; only their
repositories (uow.*) are faked in-memory, matching the existing convention
in tests/app/application/services/test_patrol_pack_service.py and
test_inference_services.py. No Postgres/Redis/network access is required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.seed_demo as seed_demo_module
from app.application.ports.crypto import OutboundNetworkPolicy
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_endpoint_service import InferenceEndpointService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.integration_server_service import MCPServerService
from app.application.services.patrol_pack_service import PatrolPackService
from app.domain.models.inference import InferencePurpose, ResourceVisibility
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import PatrolPackConfig
from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
from app.infrastructure.adapters.security_ports import FernetSecretEnvelopeAdapter
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.seed_demo import (
    DEMO_INFERENCE_ENDPOINT_NAME,
    DEMO_MCP_SERVER_ID,
    DEMO_MCP_SERVER_NAME,
    DEMO_PACK_SLUG,
    DemoInferenceEnv,
    SeedDeps,
    build_demo_pack_config,
    read_demo_inference_env,
    run_seed,
    seed_demo_pack,
    seed_inference,
    seed_mcp_tool_policies,
)

# ---------------------------------------------------------------------------
# Fakes (in-memory repositories backing the real service classes)
# ---------------------------------------------------------------------------


class _FakeMcpServerRepo:
    def __init__(self, servers: list[MCPServerRecord] | None = None) -> None:
        self.servers: dict[str, MCPServerRecord] = {s.id: s for s in (servers or [])}
        self.save_calls = 0

    async def list_all(self, scope=None):
        return list(self.servers.values())

    async def get_by_id(self, server_id, scope=None):
        return self.servers.get(server_id)

    async def get_by_name(self, name, scope=None):
        return next((s for s in self.servers.values() if s.name == name), None)

    async def exists_global_name(self, name):
        return any(s.name == name for s in self.servers.values())

    async def save(
        self,
        record,
        encrypted_url,
        url_encryption,
        encrypted_headers,
        headers_encryption,
        encrypted_env,
        env_encryption,
    ):
        self.servers[record.id] = record
        self.save_calls += 1


class _FakeScheduledJobRepo:
    def __init__(self) -> None:
        self.jobs = {}

    async def save(self, job):
        self.jobs[job.id] = job

    async def get_by_id(self, job_id, scope=None):
        return self.jobs.get(job_id)


class _FakePatrolRepo:
    def __init__(self) -> None:
        self.packs = {}
        self.save_calls = 0

    async def save_pack(self, pack):
        self.packs[pack.id] = pack
        self.save_calls += 1
        return pack

    async def get_pack(self, pack_id, scope=None, *, for_update=False):
        pack = self.packs.get(pack_id)
        if pack is None or (scope is not None and pack.owner_user_id != scope.user_id):
            return None
        return pack

    async def list_packs(self, scope, *, limit=20, offset=0):
        return [
            pack
            for pack in self.packs.values()
            if pack.owner_user_id == scope.user_id and not pack.deleted_at
        ][:limit]


class _FakeEndpointRepo:
    def __init__(self) -> None:
        self.endpoints = {}
        self.save_calls = 0

    async def get_all(self, scope=None):
        return list(self.endpoints.values())

    async def get_by_id(self, endpoint_id, scope=None):
        return self.endpoints.get(endpoint_id)

    async def save(self, endpoint, encrypted_api_key):
        self.endpoints[endpoint.id] = endpoint
        self.save_calls += 1

    async def count_models(self, endpoint_id):
        return 0


class _FakeModelRepo:
    def __init__(self) -> None:
        self.models = {}
        self.save_calls = 0

    async def get_all(self, scope=None):
        return list(self.models.values())

    async def get_all_global(self):
        return [m for m in self.models.values() if m.visibility == ResourceVisibility.GLOBAL]

    async def get_by_id(self, model_id, scope=None):
        return self.models.get(model_id)

    async def save(self, model):
        self.models[model.id] = model
        self.save_calls += 1

    async def count(self):
        return len(self.models)

    async def count_global(self):
        return len(await self.get_all_global())


class _FakeBindingRepo:
    def __init__(self) -> None:
        self.bindings = {}

    @staticmethod
    def _key(scope):
        return None if scope is None else (scope.type, scope.user_id, scope.team_id)

    async def get_effective_binding(self, purpose, scope):
        return self.bindings.get((self._key(scope), purpose)) or self.bindings.get((None, purpose))

    async def save(self, binding, scope):
        self.bindings[(self._key(scope), binding.purpose)] = binding


class FakeUow:
    """One shared in-memory 'database' handed out fresh on every `async with`."""

    def __init__(self, **repos) -> None:
        for name, repo in repos.items():
            setattr(self, name, repo)

    async def __aenter__(self):
        return self

    async def commit(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# Shared fixture: a full SeedDeps wired against the fakes above
# ---------------------------------------------------------------------------


class Repos:
    def __init__(self) -> None:
        self.mcp_server = _FakeMcpServerRepo()
        self.scheduled_job = _FakeScheduledJobRepo()
        self.patrol = _FakePatrolRepo()
        self.inference_endpoint = _FakeEndpointRepo()
        self.inference_model = _FakeModelRepo()
        self.inference_binding = _FakeBindingRepo()

    def uow_factory(self):
        return FakeUow(
            mcp_server=self.mcp_server,
            scheduled_job=self.scheduled_job,
            patrol=self.patrol,
            inference_endpoint=self.inference_endpoint,
            inference_model=self.inference_model,
            inference_binding=self.inference_binding,
        )


@pytest.fixture
def repos() -> Repos:
    return Repos()


@pytest.fixture
def deps(repos) -> SeedDeps:
    cipher = ApiKeyCipher("a" * 32)
    provider_adapter = InfrastructureInferenceProviderAdapter()
    outbound_policy = OutboundNetworkPolicy(allowed_ports=frozenset({80, 443, 8000, 8090, 11434}))
    mcp_server_service = MCPServerService(
        repos.uow_factory,
        FernetSecretEnvelopeAdapter(cipher),
        outbound_policy,
    )
    inference_endpoint_service = InferenceEndpointService(
        repos.uow_factory,
        cipher,
        outbound_policy,
        provider_adapter,
    )
    inference_model_service = InferenceModelService(
        repos.uow_factory,
        provider_adapter,
        provider_adapter,
        provider_adapter,
    )
    inference_binding_service = InferenceBindingService(repos.uow_factory, provider_adapter)
    patrol_pack_service = PatrolPackService(repos.uow_factory)
    return SeedDeps(
        mcp_server_service=mcp_server_service,
        inference_endpoint_service=inference_endpoint_service,
        inference_model_service=inference_model_service,
        inference_binding_service=inference_binding_service,
        patrol_pack_service=patrol_pack_service,
        admin_user_id="admin-1",
        demo_inference_env=None,
    )


# ---------------------------------------------------------------------------
# PatrolPackConfig model validation (no services involved)
# ---------------------------------------------------------------------------


def test_build_demo_pack_config_validates():
    config = build_demo_pack_config()
    # Round-trips through the same validation the API/service layer applies.
    revalidated = PatrolPackConfig.model_validate(config.model_dump(mode="json"))
    tools = {check.probe.tool for check in revalidated.checks}
    assert tools == {"dependency_status", "http_probe"}
    assert {check.id for check in revalidated.checks} == {
        "dependency-health",
        "endpoint-health",
        "demo-console-health",
    }
    # The third check targets the TCP dependency probe, not the HTTP probe —
    # see module docstring in api/app/seed_demo.py for why.
    demo_console_check = next(c for c in revalidated.checks if c.id == "demo-console-health")
    assert demo_console_check.probe.tool == "dependency_status"
    assert demo_console_check.probe.args == {"dependency_id": "demo-console-tcp"}
    assert demo_console_check.severity_on_fail == "warning"


# ---------------------------------------------------------------------------
# Step 1: MCP tool policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_mcp_tool_policies_creates_then_skips(deps, repos):
    first = await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")
    assert first == "create"
    saved = repos.mcp_server.servers[DEMO_MCP_SERVER_ID]
    assert saved.enabled is True
    assert len(saved.tool_policies) == 9
    assert saved.tool_policies["http_probe"].capability.value == "integration_read"
    assert saved.tool_policies["http_probe"].concurrency_group == "ops-patrol"
    save_calls_after_first = repos.mcp_server.save_calls

    second = await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")
    assert second == "skip"
    assert repos.mcp_server.save_calls == save_calls_after_first


@pytest.mark.asyncio
async def test_seed_mcp_tool_policies_repairs_existing_record(deps, repos):
    repos.mcp_server.servers["custom-id"] = MCPServerRecord(
        id="custom-id",
        name=DEMO_MCP_SERVER_NAME,
        url="http://opencitadel-ops-collector:8090/mcp",
        enabled=False,
    )
    assert (
        await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1") == "create"
    )
    assert repos.mcp_server.servers["custom-id"].enabled is True


# ---------------------------------------------------------------------------
# Step 3: demo inference endpoint/model/binding
# ---------------------------------------------------------------------------


def test_read_demo_inference_env_requires_all_three(monkeypatch):
    monkeypatch.delenv("DEMO_INFERENCE_BASE_URL", raising=False)
    monkeypatch.delenv("DEMO_INFERENCE_CREDENTIAL", raising=False)
    monkeypatch.delenv("DEMO_INFERENCE_MODEL", raising=False)
    assert read_demo_inference_env() is None

    monkeypatch.setenv("DEMO_INFERENCE_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("DEMO_INFERENCE_CREDENTIAL", "sk-test")
    monkeypatch.setenv("DEMO_INFERENCE_MODEL", "gpt-4o-mini")
    env = read_demo_inference_env()
    assert env == DemoInferenceEnv(
        base_url="https://api.example.com/v1",
        credential="sk-test",
        model="gpt-4o-mini",
        provider="openai",
    )


@pytest.mark.asyncio
async def test_seed_inference_skips_without_env(deps):
    outcome = await seed_inference(
        deps.inference_endpoint_service,
        deps.inference_model_service,
        deps.inference_binding_service,
        None,
    )
    assert outcome == "skip-no-env"


@pytest.mark.asyncio
async def test_seed_inference_creates_endpoint_model_and_binding_then_skips(deps, repos):
    env = DemoInferenceEnv(
        base_url="https://api.example.com/v1",
        credential="sk-test",
        model="gpt-4o-mini",
    )

    first = await seed_inference(
        deps.inference_endpoint_service,
        deps.inference_model_service,
        deps.inference_binding_service,
        env,
    )
    assert first == "create"
    assert len(repos.inference_endpoint.endpoints) == 1
    endpoint = next(iter(repos.inference_endpoint.endpoints.values()))
    assert endpoint.display_name == DEMO_INFERENCE_ENDPOINT_NAME
    assert len(repos.inference_model.models) == 1
    model = next(iter(repos.inference_model.models.values()))
    assert model.model_name == "gpt-4o-mini"
    binding = await repos.inference_binding.get_effective_binding(
        InferencePurpose.CHAT,
        None,
    )
    assert binding.model_id == model.id
    endpoint_saves_after_first = repos.inference_endpoint.save_calls
    model_saves_after_first = repos.inference_model.save_calls

    second = await seed_inference(
        deps.inference_endpoint_service,
        deps.inference_model_service,
        deps.inference_binding_service,
        env,
    )
    assert second == "skip"
    assert repos.inference_endpoint.save_calls == endpoint_saves_after_first
    assert repos.inference_model.save_calls == model_saves_after_first


# ---------------------------------------------------------------------------
# Step 4: demo patrol pack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_demo_pack_creates_draft_then_skips(deps, repos):
    # The seed is control-plane-only; live validation belongs to the kernel.
    await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")

    first = await seed_demo_pack(
        deps.patrol_pack_service,
        deps.mcp_server_service,
        owner_user_id="admin-1",
    )
    assert first == "create"
    assert len(repos.patrol.packs) == 1
    pack = next(iter(repos.patrol.packs.values()))
    assert pack.slug == DEMO_PACK_SLUG
    assert pack.status.value == "draft"
    assert pack.last_validated_version is None
    save_calls_after_first = repos.patrol.save_calls

    second = await seed_demo_pack(
        deps.patrol_pack_service,
        deps.mcp_server_service,
        owner_user_id="admin-1",
    )
    assert second == "skip"
    assert repos.patrol.save_calls == save_calls_after_first


@pytest.mark.asyncio
async def test_seed_demo_pack_raises_when_collector_missing(deps, repos):
    repos.mcp_server.servers.clear()
    with pytest.raises(RuntimeError, match=DEMO_MCP_SERVER_NAME):
        await seed_demo_pack(
            deps.patrol_pack_service,
            deps.mcp_server_service,
            owner_user_id="admin-1",
        )


# ---------------------------------------------------------------------------
# Full orchestration: run_seed() end to end, twice, zero new writes on rerun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_seed_end_to_end_idempotent(deps, repos):
    first = await run_seed(deps)
    assert first == {
        "mcp_tool_policies": "create",
        "inference": "skip-no-env",
        "demo_pack": "create",
    }
    save_counts_after_first = (
        repos.mcp_server.save_calls,
        repos.patrol.save_calls,
        repos.inference_endpoint.save_calls,
        repos.inference_model.save_calls,
    )

    second = await run_seed(deps)
    assert second == {
        "mcp_tool_policies": "skip",
        "inference": "skip-no-env",
        "demo_pack": "skip",
    }
    assert (
        repos.mcp_server.save_calls,
        repos.patrol.save_calls,
        repos.inference_endpoint.save_calls,
        repos.inference_model.save_calls,
    ) == save_counts_after_first


# ---------------------------------------------------------------------------
# main() lifecycle wiring: the one-shot seed only needs Postgres. Integrations
# are persisted through the first-class MCP Integration service without Redis.
# ---------------------------------------------------------------------------


class _FakeAdminUserRepo:
    def __init__(self, admin_id: str) -> None:
        self._admin_id = admin_id

    async def get_by_email(self, email):
        return SimpleNamespace(id=self._admin_id)


class _FakeAuditRepoForMain:
    async def add(self, log):
        return None


@pytest.mark.asyncio
async def test_main_seeds_integration_with_postgres_only(monkeypatch, repos):
    monkeypatch.delenv("DEMO_INFERENCE_BASE_URL", raising=False)
    monkeypatch.delenv("DEMO_INFERENCE_CREDENTIAL", raising=False)
    monkeypatch.delenv("DEMO_INFERENCE_MODEL", raising=False)

    calls: list[str] = []

    class _FakePostgres:
        session_factory = None

        async def init(self) -> None:
            calls.append("postgres.init")

        async def shutdown(self) -> None:
            calls.append("postgres.shutdown")

    fake_postgres = _FakePostgres()

    def _fake_uow_factory(**_kwargs):
        return lambda *_args, **_kwargs: FakeUow(
            user=_FakeAdminUserRepo("admin-1"),
            audit=_FakeAuditRepoForMain(),
            mcp_server=repos.mcp_server,
            scheduled_job=repos.scheduled_job,
            patrol=repos.patrol,
            inference_endpoint=repos.inference_endpoint,
            inference_model=repos.inference_model,
            inference_binding=repos.inference_binding,
        )

    monkeypatch.setattr(seed_demo_module, "create_uow_factory", _fake_uow_factory)

    await seed_demo_module.run_seed_command(
        seed_demo_module.load_deployment_settings(),
        postgres_factory=lambda _settings: fake_postgres,
    )

    assert calls == ["postgres.init", "postgres.shutdown"]
    assert DEMO_MCP_SERVER_ID in repos.mcp_server.servers
    assert len(repos.patrol.packs) == 1
