#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Idempotency + wiring tests for api/app/seed_demo.py (Phase B demo loop, Task 2).

All service classes here are the real application services; only their
repositories (uow.*) are faked in-memory, matching the existing convention
in tests/app/application/services/test_patrol_pack_service.py and
test_llm_endpoint_service.py. No Postgres/Redis/network access is required.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application.services.app_config_service import AppConfigService
from app.application.services.integration_server_service import MCPServerService
from app.application.services.llm_endpoint_service import LLMEndpointService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.patrol_pack_service import PatrolPackService
from app.domain.models.app_config import AppConfig
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.llm_model import ResourceVisibility
from app.domain.models.patrol import PatrolPackConfig
from app.domain.models.scope import OwnerScope
from app.domain.models.skill import Skill
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
import app.seed_demo as seed_demo_module
from app.seed_demo import (
    DEMO_LLM_ENDPOINT_NAME,
    DEMO_MCP_SERVER_NAME,
    DEMO_OUTPUT_SCHEMA_HASH,
    DEMO_PACK_SLUG,
    DemoLLMEnv,
    SeedDeps,
    build_demo_pack_config,
    read_demo_llm_env,
    run_seed,
    seed_demo_pack,
    seed_feature_flags,
    seed_llm,
    seed_mcp_tool_policies,
)


# ---------------------------------------------------------------------------
# Fakes (in-memory repositories backing the real service classes)
# ---------------------------------------------------------------------------


class _FakeAppConfigRepository:
    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()

    async def load_global(self):
        return self._config.model_copy(deep=True)

    async def save_global(self, app_config, *, changed_by=None, note=""):
        self._config = app_config.model_copy(deep=True)


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

    async def save(self, record, encrypted_url, url_encryption, encrypted_headers, headers_encryption, encrypted_env, env_encryption):
        self.servers[record.id] = record
        self.save_calls += 1


class _FakeSkillRepo:
    def __init__(self, skill: Skill) -> None:
        self._skill = skill

    async def get_by_slug(self, slug):
        return self._skill if slug == self._skill.slug else None

    async def get_by_id(self, skill_id, scope=None):
        return self._skill if skill_id == self._skill.id else None


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


class _FakeModelPreferenceRepo:
    def __init__(self) -> None:
        self.prefs = {}

    @staticmethod
    def _key(scope):
        return None if scope is None else (scope.type, scope.user_id, scope.team_id)

    async def get_model_id(self, scope):
        return self.prefs.get(self._key(scope))

    async def set_model_id(self, scope, model_id):
        self.prefs[self._key(scope)] = model_id


class FakeUow:
    """One shared in-memory 'database' handed out fresh on every `async with`."""

    def __init__(self, **repos) -> None:
        for name, repo in repos.items():
            setattr(self, name, repo)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCollectorValidator:
    """Stands in for MCPPatrolCollectorValidator's live MCP calls."""

    def __init__(self, tools: set[str], *, dry_run_ok: bool = True) -> None:
        self._tools = tools
        self._dry_run_ok = dry_run_ok

    async def get_capabilities(self, server):
        return {
            "enabled_tools": sorted(self._tools | {"get_capabilities"}),
            "output_schema_hashes": {tool: DEMO_OUTPUT_SCHEMA_HASH for tool in self._tools},
            "overall_capability_hash": "c" * 64,
        }

    async def dry_run(self, server, config):
        return {"mode": "fake", "ok": self._dry_run_ok, "probes": []}


# ---------------------------------------------------------------------------
# Shared fixture: a full SeedDeps wired against the fakes above
# ---------------------------------------------------------------------------


class Repos:
    def __init__(self) -> None:
        self.app_config = _FakeAppConfigRepository()
        collector = MCPServerRecord(
            id="collector-1",
            name=DEMO_MCP_SERVER_NAME,
            url="http://opencitadel-ops-collector:8090/mcp",
            enabled=False,
        )
        self.mcp_server = _FakeMcpServerRepo([collector])
        self.skill = _FakeSkillRepo(Skill(id="skill-1", name="Ops Patrol", slug="ops-patrol"))
        self.scheduled_job = _FakeScheduledJobRepo()
        self.patrol = _FakePatrolRepo()
        self.llm_endpoint = _FakeEndpointRepo()
        self.llm_model = _FakeModelRepo()
        self.llm_model_preference = _FakeModelPreferenceRepo()

    def uow_factory(self):
        return FakeUow(
            mcp_server=self.mcp_server,
            skill=self.skill,
            scheduled_job=self.scheduled_job,
            patrol=self.patrol,
            llm_endpoint=self.llm_endpoint,
            llm_model=self.llm_model,
            llm_model_preference=self.llm_model_preference,
        )


@pytest.fixture
def repos() -> Repos:
    return Repos()


@pytest.fixture
def enabled_ops_patrol_config(monkeypatch, repos):
    """Patrol Pack operations gate on get_runtime_config(); keep it enabled
    the same way test_patrol_pack_service.py does, without depending on the
    real AppConfigProvider warmup machinery."""
    config = AppConfig()
    config.feature_flags.enable_ops_patrol = True
    monkeypatch.setattr(
        "app.application.services.patrol_pack_service.get_runtime_config",
        lambda: config,
    )
    return config


@pytest.fixture
def deps(repos, enabled_ops_patrol_config) -> SeedDeps:
    cipher = ApiKeyCipher("a" * 32)
    app_config_service = AppConfigService(
        app_config_repository=repos.app_config,
        mcp_server_service=MCPServerService(repos.uow_factory, cipher),
    )
    mcp_server_service = MCPServerService(repos.uow_factory, cipher)
    llm_endpoint_service = LLMEndpointService(repos.uow_factory, cipher)
    llm_model_service = LLMModelService(repos.uow_factory, cipher)
    patrol_pack_service = PatrolPackService(
        repos.uow_factory,
        collector_validator=_FakeCollectorValidator({"dependency_status", "http_probe"}),
    )
    return SeedDeps(
        app_config_service=app_config_service,
        mcp_server_service=mcp_server_service,
        llm_endpoint_service=llm_endpoint_service,
        llm_model_service=llm_model_service,
        patrol_pack_service=patrol_pack_service,
        admin_user_id="admin-1",
        demo_llm_env=None,
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
# Step 1: feature_flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_feature_flags_creates_then_skips(deps, repos):
    first = await seed_feature_flags(deps.app_config_service, changed_by="admin-1")
    assert first == "create"
    saved = await repos.app_config.load_global()
    assert saved.feature_flags.enable_ops_patrol is True
    assert saved.feature_flags.enable_ops_patrol_fixture_replay is False

    second = await seed_feature_flags(deps.app_config_service, changed_by="admin-1")
    assert second == "skip"


# ---------------------------------------------------------------------------
# Step 2: MCP tool policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_mcp_tool_policies_creates_then_skips(deps, repos):
    first = await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")
    assert first == "create"
    saved = repos.mcp_server.servers["collector-1"]
    assert saved.enabled is True
    assert len(saved.tool_policies) == 9
    assert saved.tool_policies["http_probe"].capability.value == "integration_read"
    assert saved.tool_policies["http_probe"].concurrency_group == "ops-patrol"
    save_calls_after_first = repos.mcp_server.save_calls

    second = await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")
    assert second == "skip"
    assert repos.mcp_server.save_calls == save_calls_after_first


@pytest.mark.asyncio
async def test_seed_mcp_tool_policies_missing_server_raises(deps, repos):
    repos.mcp_server.servers.clear()
    with pytest.raises(RuntimeError, match=DEMO_MCP_SERVER_NAME):
        await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")


# ---------------------------------------------------------------------------
# Step 3: demo LLM endpoint/model
# ---------------------------------------------------------------------------


def test_read_demo_llm_env_requires_all_three(monkeypatch):
    monkeypatch.delenv("DEMO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DEMO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_LLM_MODEL", raising=False)
    assert read_demo_llm_env() is None

    monkeypatch.setenv("DEMO_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("DEMO_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DEMO_LLM_MODEL", "gpt-4o-mini")
    env = read_demo_llm_env()
    assert env == DemoLLMEnv(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        provider="openai",
    )


@pytest.mark.asyncio
async def test_seed_llm_skips_without_env(deps):
    outcome = await seed_llm(deps.llm_endpoint_service, deps.llm_model_service, None)
    assert outcome == "skip-no-env"


@pytest.mark.asyncio
async def test_seed_llm_creates_endpoint_model_and_default_then_skips(deps, repos):
    env = DemoLLMEnv(base_url="https://api.example.com/v1", api_key="sk-test", model="gpt-4o-mini")

    first = await seed_llm(deps.llm_endpoint_service, deps.llm_model_service, env)
    assert first == "create"
    assert len(repos.llm_endpoint.endpoints) == 1
    endpoint = next(iter(repos.llm_endpoint.endpoints.values()))
    assert endpoint.display_name == DEMO_LLM_ENDPOINT_NAME
    assert len(repos.llm_model.models) == 1
    model = next(iter(repos.llm_model.models.values()))
    assert model.model_name == "gpt-4o-mini"
    default_id = await repos.llm_model_preference.get_model_id(None)
    assert default_id == model.id
    endpoint_saves_after_first = repos.llm_endpoint.save_calls
    model_saves_after_first = repos.llm_model.save_calls

    second = await seed_llm(deps.llm_endpoint_service, deps.llm_model_service, env)
    assert second == "skip"
    assert repos.llm_endpoint.save_calls == endpoint_saves_after_first
    assert repos.llm_model.save_calls == model_saves_after_first


# ---------------------------------------------------------------------------
# Step 4: demo patrol pack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_demo_pack_creates_validates_activates_then_skips(deps, repos):
    # ops-collector must already be enabled with policies for validation to pass.
    await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")

    first = await seed_demo_pack(
        deps.patrol_pack_service, deps.mcp_server_service, owner_user_id="admin-1"
    )
    assert first == "create"
    assert len(repos.patrol.packs) == 1
    pack = next(iter(repos.patrol.packs.values()))
    assert pack.slug == DEMO_PACK_SLUG
    assert pack.status.value == "active"
    assert pack.last_validated_version == pack.version
    save_calls_after_first = repos.patrol.save_calls

    second = await seed_demo_pack(
        deps.patrol_pack_service, deps.mcp_server_service, owner_user_id="admin-1"
    )
    assert second == "skip"
    assert repos.patrol.save_calls == save_calls_after_first


@pytest.mark.asyncio
async def test_seed_demo_pack_raises_when_collector_missing(deps, repos):
    repos.mcp_server.servers.clear()
    with pytest.raises(RuntimeError, match=DEMO_MCP_SERVER_NAME):
        await seed_demo_pack(deps.patrol_pack_service, deps.mcp_server_service, owner_user_id="admin-1")


@pytest.mark.asyncio
async def test_seed_demo_pack_surfaces_validation_failure(deps, repos):
    await seed_mcp_tool_policies(deps.mcp_server_service, actor_user_id="admin-1")
    # Swap in a collector validator whose live dry run fails, simulating an
    # unreachable/misconfigured Collector at seed time.
    deps.patrol_pack_service._collector_validator = _FakeCollectorValidator(
        {"dependency_status", "http_probe"}, dry_run_ok=False
    )
    with pytest.raises(RuntimeError, match="failed validation"):
        await seed_demo_pack(deps.patrol_pack_service, deps.mcp_server_service, owner_user_id="admin-1")


# ---------------------------------------------------------------------------
# Full orchestration: run_seed() end to end, twice, zero new writes on rerun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_seed_end_to_end_idempotent(deps, repos):
    first = await run_seed(deps)
    assert first == {
        "feature_flags": "create",
        "mcp_tool_policies": "create",
        "llm": "skip-no-env",
        "demo_pack": "create",
    }
    save_counts_after_first = (
        repos.mcp_server.save_calls,
        repos.patrol.save_calls,
        repos.llm_endpoint.save_calls,
        repos.llm_model.save_calls,
    )

    second = await run_seed(deps)
    assert second == {
        "feature_flags": "skip",
        "mcp_tool_policies": "skip",
        "llm": "skip-no-env",
        "demo_pack": "skip",
    }
    assert (
        repos.mcp_server.save_calls,
        repos.patrol.save_calls,
        repos.llm_endpoint.save_calls,
        repos.llm_model.save_calls,
    ) == save_counts_after_first


# ---------------------------------------------------------------------------
# main() lifecycle wiring (Phase B final review, C1): the fix is (a) main()
# now initializes Redis after Postgres and shuts it down (paired) in a
# finally, and (b) main() wires SeedDeps.notify_config_invalidate to the real
# publish_config_invalidate(). This test exercises the real main() control
# flow end to end, faking only the process-lifetime seams (Postgres/Redis
# singletons, DBUnitOfWork, the app-config repository/provider factories,
# and MCPPatrolCollectorValidator) -- everything downstream (AppConfigService,
# MCPServerService, PatrolPackService, LLMEndpointService/LLMModelService,
# run_seed() itself) is the real application code.
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
async def test_main_initializes_redis_and_publishes_config_invalidate(
    monkeypatch, repos, enabled_ops_patrol_config
):
    monkeypatch.delenv("DEMO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DEMO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_LLM_MODEL", raising=False)

    calls: list[str] = []

    class _FakePostgres:
        session_factory = None

        async def init(self) -> None:
            calls.append("postgres.init")

        async def shutdown(self) -> None:
            calls.append("postgres.shutdown")

    class _FakeRedis:
        async def init(self) -> None:
            calls.append("redis.init")

        async def shutdown(self) -> None:
            calls.append("redis.shutdown")

    fake_postgres = _FakePostgres()
    fake_redis = _FakeRedis()
    monkeypatch.setattr(seed_demo_module, "get_postgres", lambda: fake_postgres)
    monkeypatch.setattr(seed_demo_module, "get_redis", lambda: fake_redis)

    def _fake_uow_ctor(session_factory=None, authorization_context=None):
        return FakeUow(
            user=_FakeAdminUserRepo("admin-1"),
            audit=_FakeAuditRepoForMain(),
            mcp_server=repos.mcp_server,
            skill=repos.skill,
            scheduled_job=repos.scheduled_job,
            patrol=repos.patrol,
            llm_endpoint=repos.llm_endpoint,
            llm_model=repos.llm_model,
            llm_model_preference=repos.llm_model_preference,
        )

    monkeypatch.setattr(seed_demo_module, "DBUnitOfWork", _fake_uow_ctor)
    monkeypatch.setattr(seed_demo_module, "create_app_config_repository", lambda: repos.app_config)

    class _FakeProvider:
        async def get(self, force_reload: bool = False):
            return None

    monkeypatch.setattr(seed_demo_module, "create_app_config_provider", lambda: _FakeProvider())
    monkeypatch.setattr(
        seed_demo_module,
        "MCPPatrolCollectorValidator",
        lambda adapter: _FakeCollectorValidator({"dependency_status", "http_probe"}),
    )

    publish_calls: list[None] = []

    async def _fake_publish() -> None:
        publish_calls.append(None)

    monkeypatch.setattr(seed_demo_module, "publish_config_invalidate", _fake_publish)

    await seed_demo_module.main()

    # Redis initialized after Postgres, both shut down (paired, reverse order).
    assert calls == ["postgres.init", "redis.init", "redis.shutdown", "postgres.shutdown"]
    # The explicit, awaited notification fired exactly once after seeding.
    assert publish_calls == [None]
    # Sanity: the real seed pipeline actually ran end to end through the fakes.
    assert len(repos.patrol.packs) == 1
    saved_flags = (await repos.app_config.load_global()).feature_flags
    assert saved_flags.enable_ops_patrol is True


# ---------------------------------------------------------------------------
# Critical fix (Phase B final review, C1): run_seed() must explicitly await
# a cross-process config-invalidate notification once seeding is done,
# rather than relying on AppConfigService's internal fire-and-forget
# publish_config_invalidate() task (which silently no-ops when Redis was
# never initialized -- exactly seed_demo.py's original main(), which only
# called postgres.init()).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_seed_awaits_notify_config_invalidate_once(deps):
    calls: list[None] = []

    async def fake_notify() -> None:
        calls.append(None)

    deps.notify_config_invalidate = fake_notify

    await run_seed(deps)
    assert len(calls) == 1

    # Unconditional: still fires on the fully-idempotent "everything already
    # seeded" rerun, so a seed re-run always re-syncs any long-running API
    # process that somehow missed the first notification.
    await run_seed(deps)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_seed_default_notify_hook_is_noop(deps):
    # SeedDeps.notify_config_invalidate defaults to the same no-op used by
    # refresh_runtime_config; run_seed() must not blow up when the caller
    # (e.g. some other future test) doesn't wire a real notifier.
    result = await run_seed(deps)
    assert result["feature_flags"] == "create"
