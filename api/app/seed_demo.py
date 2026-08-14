#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed a runnable Ops Patrol demo (Phase B "ten minute demo loop", Task 2).

Container entrypoint:  docker compose exec opencitadel-api python -m app.seed_demo

Automates the manual steps from docs/tutorials/06-ops-patrol.md end to end:

1. Enable ``feature_flags.enable_ops_patrol`` (read-merge-write; leaves
   ``enable_ops_patrol_fixture_replay``/``enable_ops_patrol_remediation`` untouched).
2. Enable the ``ops-collector`` MCP server and persist its nine fixed
   read-only Tool Policies (values from docs/operations/ops-patrol.md).
3. Optionally register a demo LLM endpoint/model from
   DEMO_LLM_BASE_URL/DEMO_LLM_API_KEY/DEMO_LLM_MODEL/DEMO_LLM_PROVIDER env
   vars, and set it as the system default model.
4. Create, validate, and activate a custom "Demo Governance Patrol" Pack
   with three checks (see build_demo_pack_config()).
5. Print demo guidance, including how to manufacture a Finding on demand.

Every step is idempotent: re-running this module makes zero additional
writes once the demo state already exists ("[skip] ..." is printed instead).

Fixed gap (coordinator review round 1, F1): MCPServerService.update_server()
re-validates the server URL through
app/domain/utils/mcp_url.py:validate_mcp_http_url(), which used to call
resolve_outbound_url() *without* forwarding Settings.outbound_allowed_ports
(unlike LLMEndpointService, which already did), falling back to a hardcoded
``DEFAULT_OUTBOUND_PORTS = {80, 443, 8080, 8443}`` that did not include 8090
-- the port docker-compose.yml deploys ``opencitadel-ops-collector`` on. That
blocked the officially documented admin flow in docs/operations/ops-patrol.md
("Register the MCP Server" -> ``POST /api/app-config/mcp-servers/ops-collector
/update``, which hits this exact code path) just as much as this seed script.
Fixed at the source instead of working around it here: mcp_url.py now accepts
an ``allowed_ports`` override, integration_server_service.py and
domain/services/tools/mcp.py (the live MCP-connect path, which the same
validator gates and which validate_pack()/activate_pack() below depend on)
both pass ``Settings.outbound_allowed_ports``, and that setting's default
(core/config.py) now includes 8090/8091. seed_mcp_tool_policies() below goes
through the normal ``MCPServerService.update_server()`` path again -- the same
one the documented admin flow uses.

Finding-manufacture semantics (see build_demo_pack_config() docstring for the
full reasoning): stopping the `ops-console` demo container makes the
`demo-console-health` check fail via the `dependency_status` probe
(`demo-console-tcp`), not via `http_probe`. Reading
ops-collector/src/opencitadel_ops_collector/collector.py shows why:

- `dependency_status` catches the refused TCP connection internally
  (`except OSError`) and still returns envelope ``status="ok"`` with
  ``data.healthy=false`` — this flows deterministically into
  PatrolAssertionEngine, which evaluates the `$.healthy eq true` assertion,
  finds it failed, and produces a FAIL PatrolCheckResult (severity from the
  check's own `severity_on_fail`) -> a Finding.
- `http_probe` against a fully-stopped target instead lets the
  `httpx.ConnectError` propagate out of `_execute()`'s retry loop, which is
  caught by the `except (httpx.NetworkError, ConnectionError)` branch and
  returns envelope ``status="unavailable"``. PatrolAssertionEngine
  short-circuits on any ``probe_status != PatrolProbeStatus.OK`` *before*
  evaluating assertions, returning a generic ERROR result with a hardcoded
  WARNING severity — it still becomes a Finding (WARN/FAIL/ERROR all do, see
  patrol_run_service.py), but the assertion/severity_on_fail configuration
  the Pack author wrote is never actually exercised, and the outcome depends
  on the patrol agent faithfully mirroring the Collector's literal
  "unavailable" string into `probe_status` rather than on deterministic
  server-side evaluation.

For a demo whose "stop a container -> see a Finding" story must be reliable
and easy to explain, `dependency_status`/TCP is the better choice. This
seed's third check therefore targets `demo-console-tcp` instead of the
`demo-console` HTTP probe suggested as the initial idea in the task brief.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.application.security.authorization_context import authorization_scope
from app.application.services.app_config_repository_factory import create_app_config_repository
from app.application.services.app_config_service import AppConfigService
from app.application.services.audit_service import AuditService
from app.application.services.config_provider import create_app_config_provider
from app.application.services.integration_server_service import MCPServerService
from app.application.services.llm_endpoint_service import LLMEndpointService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.patrol_collector_validator import MCPPatrolCollectorValidator
from app.application.services.patrol_pack_service import PatrolPackService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMModel, LLMProvider, ResourceVisibility
from app.domain.models.patrol import (
    PATROL_PROBE_TOOLS,
    PatrolAssertion,
    PatrolCheck,
    PatrolPackConfig,
    PatrolProbe,
    PatrolScope,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.infrastructure.adapters.connection_pool import InfrastructureMCPConnectionPoolAdapter
from app.infrastructure.external.app_config_notifier import publish_config_invalidate
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)

DEMO_MCP_SERVER_NAME = "ops-collector"
DEMO_LLM_ENDPOINT_NAME = "Demo Endpoint"
DEMO_LLM_MODEL_NAME = "Demo Model"
DEMO_PACK_NAME = "Demo Governance Patrol"
DEMO_PACK_SLUG = "demo-governance-patrol"

# All nine ops-collector tools that Ops Patrol is allowed to call, fixed
# read-only. Values copied verbatim from docs/operations/ops-patrol.md
# (`## Register the MCP Server`).
DEMO_TOOL_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.INTEGRATION_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
    concurrency_group="ops-patrol",
)

# ops-collector/src/opencitadel_ops_collector/capabilities.py:capability_manifest()
# hashes the *same* CollectorEnvelope JSON schema for every tool name
# (`output_hashes = {name: canonical_hash(output_schema) for name in
# TOOL_INPUT_MODELS}`), so one hash is valid for every check regardless of
# which tool it probes. This is the exact value already baked into
# api/app/application/patrol_templates/kubernetes_daily_patrol.v1.yaml for
# all ten of its checks (k8s_workload_summary / k8s_recent_events /
# prom_query / certificate_status / backup_status / dependency_status /
# http_probe). PatrolPackService.validate_pack() re-verifies this against
# the live Collector's get_capabilities() before the Pack may activate, so a
# stale constant here fails closed rather than silently activating.
DEMO_OUTPUT_SCHEMA_HASH = "03af4b9122da051ecb5e5d707552f27b09875d46b1124b671bb350fad06b28e1"

DEMO_TARGET_REF = "opencitadel-local"
DEMO_CLUSTER = "opencitadel-demo"
DEMO_NAMESPACE = "opencitadel"
DEMO_ENVIRONMENT = "dev"


def _desired_tool_policies() -> dict[str, ToolExecutionPolicy]:
    tool_names = sorted(PATROL_PROBE_TOOLS | {"get_capabilities"})
    return {name: DEMO_TOOL_POLICY for name in tool_names}


def build_demo_pack_config() -> PatrolPackConfig:
    """Custom three-check Patrol Pack config for the demo loop.

    See the module docstring for why check #3 probes `demo-console-tcp` via
    `dependency_status` rather than the `demo-console` `http_probe`.
    """
    return PatrolPackConfig(
        target_ref=DEMO_TARGET_REF,
        timezone="UTC",
        scope=PatrolScope(
            cluster=DEMO_CLUSTER,
            namespaces=[DEMO_NAMESPACE],
            environment=DEMO_ENVIRONMENT,
        ),
        checks=[
            PatrolCheck(
                id="dependency-health",
                title="PostgreSQL dependency health",
                probe=PatrolProbe(
                    tool="dependency_status",
                    args={"dependency_id": "primary-dependencies"},
                    output_schema_hash=DEMO_OUTPUT_SCHEMA_HASH,
                ),
                assertions=[
                    PatrolAssertion(
                        id="dependency-healthy",
                        field="$.healthy",
                        op="eq",
                        value=True,
                        status_on_failure="fail",
                        message="Primary dependency group (PostgreSQL) is unhealthy",
                    ),
                ],
                severity_on_fail="critical",
                required_evidence=["summary"],
            ),
            PatrolCheck(
                id="endpoint-health",
                title="OpenCitadel API endpoint health",
                probe=PatrolProbe(
                    tool="http_probe",
                    args={"probe_id": "primary-endpoint"},
                    output_schema_hash=DEMO_OUTPUT_SCHEMA_HASH,
                ),
                assertions=[
                    PatrolAssertion(
                        id="endpoint-healthy",
                        field="$.healthy",
                        op="eq",
                        value=True,
                        status_on_failure="fail",
                        message="Primary API endpoint returned a non-success status",
                    ),
                ],
                severity_on_fail="critical",
                required_evidence=["summary"],
            ),
            PatrolCheck(
                id="demo-console-health",
                title="Demo Ops Console dependency health",
                probe=PatrolProbe(
                    tool="dependency_status",
                    args={"dependency_id": "demo-console-tcp"},
                    output_schema_hash=DEMO_OUTPUT_SCHEMA_HASH,
                ),
                assertions=[
                    PatrolAssertion(
                        id="demo-console-healthy",
                        field="$.healthy",
                        op="eq",
                        value=True,
                        status_on_failure="fail",
                        message=(
                            "Demo Ops Console is unreachable — stop/start the "
                            "`ops-console` container to demo a Finding"
                        ),
                    ),
                ],
                severity_on_fail="warning",
                required_evidence=["summary"],
            ),
        ],
    )


@dataclass
class DemoLLMEnv:
    base_url: str
    api_key: str
    model: str
    provider: str = "openai"


def read_demo_llm_env() -> Optional[DemoLLMEnv]:
    base_url = os.environ.get("DEMO_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("DEMO_LLM_API_KEY", "").strip()
    model = os.environ.get("DEMO_LLM_MODEL", "").strip()
    provider = os.environ.get("DEMO_LLM_PROVIDER", "openai").strip() or "openai"
    if not (base_url and api_key and model):
        return None
    return DemoLLMEnv(base_url=base_url, api_key=api_key, model=model, provider=provider)


async def _noop_refresh() -> None:
    return None


@dataclass
class SeedDeps:
    app_config_service: AppConfigService
    mcp_server_service: MCPServerService
    llm_endpoint_service: LLMEndpointService
    llm_model_service: LLMModelService
    patrol_pack_service: PatrolPackService
    admin_user_id: str
    demo_llm_env: Optional[DemoLLMEnv] = None
    refresh_runtime_config: Callable[[], Awaitable[None]] = field(default=_noop_refresh)
    # Explicit cross-process cache-invalidation hook, called once after every
    # seed run completes (see run_seed()). Defaults to a no-op for tests;
    # main() wires the real publish_config_invalidate() here.
    #
    # Why this can't be left to AppConfigService's own fire-and-forget
    # notification (app_config_service.py:_notify_config_invalidate()):
    # that path does `loop.create_task(publish_config_invalidate())` and
    # returns immediately without awaiting it. In a short-lived one-shot
    # script driven by `asyncio.run(main())`, the event loop is torn down
    # right after `main()` returns, so a task merely *scheduled* there is
    # never guaranteed to actually run before the interpreter exits -- and
    # even when it does run, publish_config_invalidate() swallows any
    # exception (e.g. Redis never initialized) into a warning log, so seeding
    # would silently "succeed" while never notifying already-running API
    # processes to drop their stale `_sync_cache`. Awaiting it explicitly
    # here, once, after all writes are done, makes the notification a
    # hard-required, observable step of the seed run instead of a best-effort
    # side effect.
    notify_config_invalidate: Callable[[], Awaitable[None]] = field(default=_noop_refresh)


async def seed_feature_flags(app_config_service: AppConfigService, *, changed_by: str) -> str:
    current = await app_config_service.get_section("feature_flags")
    if current.enable_ops_patrol:
        return "skip"
    payload = current.model_dump(mode="json")
    payload["enable_ops_patrol"] = True
    await app_config_service.update_section(
        "feature_flags",
        payload,
        changed_by=changed_by,
        is_admin=True,
    )
    return "create"


async def seed_mcp_tool_policies(mcp_server_service: MCPServerService, *, actor_user_id: str) -> str:
    """Enable ops-collector and persist its nine tool policies.

    Goes through the normal ``MCPServerService.update_server()`` path -- the
    same one the documented admin flow (docs/operations/ops-patrol.md
    "Register the MCP Server") uses. This used to bypass the service and
    write through the repository directly to work around
    update_server()'s outbound-port revalidation rejecting the Collector's
    port (8090); that gap is now fixed at the source (see module docstring,
    "Fixed gap (coordinator review round 1, F1)").
    """
    servers = await mcp_server_service.list_servers(mask=False)
    target = next((server for server in servers if server.name == DEMO_MCP_SERVER_NAME), None)
    if target is None:
        raise RuntimeError(
            f"MCP server '{DEMO_MCP_SERVER_NAME}' not found. It should already exist from the "
            "config.yaml -> app_configs blob -> mcp_servers table migration (api/app/migrate.py); "
            "run `docker compose exec opencitadel-api python -m app.migrate` first."
        )
    desired = _desired_tool_policies()
    if target.enabled and target.tool_policies == desired:
        return "skip"
    updated = MCPServerRecord(
        id=target.id,
        name=target.name,
        transport=target.transport,
        enabled=True,
        description=target.description,
        command=target.command,
        args=target.args,
        url=target.url,
        headers=target.headers,
        env=target.env,
        tool_policies=desired,
        owner_user_id=target.owner_user_id,
        team_id=target.team_id,
        visibility=target.visibility,
    )
    await mcp_server_service.update_server(
        target.id,
        updated,
        actor_user_id=actor_user_id,
        is_admin=True,
    )
    return "create"


async def seed_llm(
    llm_endpoint_service: LLMEndpointService,
    llm_model_service: LLMModelService,
    env: Optional[DemoLLMEnv],
) -> str:
    if env is None:
        return "skip-no-env"
    endpoints = await llm_endpoint_service.list_endpoints(scope=None)
    existing = next((item for item in endpoints if item.display_name == DEMO_LLM_ENDPOINT_NAME), None)
    if existing is not None:
        return "skip"
    endpoint = LLMEndpoint(
        display_name=DEMO_LLM_ENDPOINT_NAME,
        provider=LLMProvider(env.provider),
        base_url=env.base_url,
        api_key=env.api_key,
        visibility=ResourceVisibility.GLOBAL,
    )
    created_endpoint = await llm_endpoint_service.create_endpoint(
        endpoint,
        scope=None,
        allow_global_mutation=True,
    )
    model = LLMModel(
        endpoint_id=created_endpoint.id,
        display_name=DEMO_LLM_MODEL_NAME,
        provider=LLMProvider(env.provider),
        model_name=env.model,
        visibility=ResourceVisibility.GLOBAL,
    )
    created_model = await llm_model_service.create_model(
        model,
        scope=None,
        allow_global_mutation=True,
    )
    await llm_model_service.set_default(created_model.id)
    return "create"


async def seed_demo_pack(
    patrol_pack_service: PatrolPackService,
    mcp_server_service: MCPServerService,
    *,
    owner_user_id: str,
) -> str:
    scope = OwnerScope.personal(owner_user_id)
    existing_packs = await patrol_pack_service.list_packs(scope, limit=100)
    if any(pack.slug == DEMO_PACK_SLUG and not pack.deleted_at for pack in existing_packs):
        return "skip"
    servers = await mcp_server_service.list_servers(mask=False)
    collector = next((server for server in servers if server.name == DEMO_MCP_SERVER_NAME), None)
    if collector is None:
        raise RuntimeError(f"MCP server '{DEMO_MCP_SERVER_NAME}' not found; run seed_mcp_tool_policies() first")

    pack = await patrol_pack_service.create_pack(
        owner_user_id=owner_user_id,
        scope=scope,
        name=DEMO_PACK_NAME,
        mcp_server_id=collector.id,
        config=build_demo_pack_config(),
        slug=DEMO_PACK_SLUG,
    )
    validated = await patrol_pack_service.validate_pack(pack.id, scope, owner_user_id)
    if not validated.validation_summary.get("ok"):
        raise RuntimeError(
            f"Demo pack failed validation against the live Collector: "
            f"{validated.validation_summary.get('errors')}"
        )
    await patrol_pack_service.activate_pack(pack.id, scope, owner_user_id)
    return "create"


def _log(step: str, outcome: str) -> None:
    prefix = "[skip]" if outcome.startswith("skip") else "[create]"
    print(f"{prefix} {step}: {outcome}")


def _print_demo_guide(results: dict[str, str]) -> None:
    print()
    print("=" * 72)
    print("OpenCitadel demo data is ready.")
    print("=" * 72)
    print("  1. Log in, open 'Ops Patrol', and select "
          f"'{DEMO_PACK_NAME}'.")
    print("  2. Click 'Run now' and wait for the Run to reach a terminal state.")
    print("  3. Open /admin/governance for the governance overview dashboard.")
    print()
    print("To manufacture a Finding on demand:")
    print("  docker compose stop ops-console")
    print("  ...then Run now again. The 'demo-console-health' check (a")
    print("  dependency_status probe against demo-console-tcp) fails")
    print("  deterministically: the refused TCP dial still comes back as")
    print("  envelope status=\"ok\" with data.healthy=false, and the")
    print("  server-side assertion turns that into a FAIL -> Finding.")
    print("  (An http_probe against a fully-stopped target instead returns")
    print("  status=\"unavailable\", which skips assertion evaluation")
    print("  entirely and is less demo-friendly — see api/app/seed_demo.py")
    print("  module docstring for the full read-the-code justification.)")
    print("  docker compose start ops-console   # restore before the next demo")
    print()
    if results.get("llm") == "skip-no-env":
        print("No demo LLM was registered (DEMO_LLM_BASE_URL/DEMO_LLM_API_KEY/")
        print("DEMO_LLM_MODEL not set). Add one later in Settings -> Models.")
    print("=" * 72)


async def run_seed(deps: SeedDeps) -> dict[str, str]:
    results: dict[str, str] = {}

    results["feature_flags"] = await seed_feature_flags(
        deps.app_config_service, changed_by=deps.admin_user_id
    )
    _log("feature_flags.enable_ops_patrol", results["feature_flags"])

    results["mcp_tool_policies"] = await seed_mcp_tool_policies(
        deps.mcp_server_service, actor_user_id=deps.admin_user_id
    )
    _log("MCP server 'ops-collector' enabled + 9 tool policies", results["mcp_tool_policies"])

    # Only the feature_flags write above goes through AppConfigService.
    # update_section(), which is what actually calls invalidate_runtime_config()
    # (seed_mcp_tool_policies() calls MCPServerService.update_server() directly,
    # which does not touch that cache). patrol_pack_service._require_enabled()
    # reads the cache synchronously, so refresh it before touching the Pack
    # service regardless -- cheap, and correct even if a future step here starts
    # invalidating it too.
    await deps.refresh_runtime_config()

    results["llm"] = await seed_llm(deps.llm_endpoint_service, deps.llm_model_service, deps.demo_llm_env)
    _log("Demo LLM endpoint/model", results["llm"])

    results["demo_pack"] = await seed_demo_pack(
        deps.patrol_pack_service, deps.mcp_server_service, owner_user_id=deps.admin_user_id
    )
    _log(f"Patrol pack '{DEMO_PACK_NAME}' ({DEMO_PACK_SLUG})", results["demo_pack"])

    # Explicit, awaited cross-process invalidation -- see SeedDeps.notify_config_invalidate
    # docstring above for why this can't rely on AppConfigService's internal
    # fire-and-forget notification alone.
    await deps.notify_config_invalidate()

    _print_demo_guide(results)
    return results


async def main() -> None:
    settings = get_settings()
    postgres = get_postgres()
    await postgres.init()
    try:
        # Cross-process config-invalidate notification (SeedDeps.notify_config_invalidate,
        # awaited at the end of run_seed()) goes over Redis pub/sub, so it needs an
        # initialized Redis client too -- this is a short-lived script, not the long-running
        # API process, so nothing else in this module's process would ever init it.
        redis = get_redis()
        await redis.init()
        try:
            with authorization_scope(AuthorizationContext.system("seed-demo")):
                uow_factory = lambda: DBUnitOfWork(session_factory=postgres.session_factory)  # noqa: E731

                cipher = ApiKeyCipher(
                    settings.api_key_secret,
                    key_id=settings.api_key_secret_id,
                    previous_secrets=settings.api_key_previous_secrets,
                )
                audit_service = AuditService(uow_factory)
                mcp_server_service = MCPServerService(uow_factory, cipher, audit_service)
                app_config_service = AppConfigService(
                    app_config_repository=create_app_config_repository(),
                    mcp_server_service=mcp_server_service,
                )
                provider = create_app_config_provider()
                await provider.get()

                collector_validator = MCPPatrolCollectorValidator(InfrastructureMCPConnectionPoolAdapter())
                patrol_pack_service = PatrolPackService(uow_factory, audit_service, collector_validator)
                llm_model_service = LLMModelService(uow_factory, cipher)
                llm_endpoint_service = LLMEndpointService(uow_factory, cipher)

                async with uow_factory() as uow:
                    admin = await uow.user.get_by_email(settings.bootstrap_admin_email)
                if admin is None:
                    raise SystemExit(
                        f"Bootstrap admin user '{settings.bootstrap_admin_email}' not found. "
                        "Start the API once first so bootstrap_admin_user() seeds it, then re-run."
                    )

                async def _refresh() -> None:
                    await provider.get()

                deps = SeedDeps(
                    app_config_service=app_config_service,
                    mcp_server_service=mcp_server_service,
                    llm_endpoint_service=llm_endpoint_service,
                    llm_model_service=llm_model_service,
                    patrol_pack_service=patrol_pack_service,
                    admin_user_id=admin.id,
                    demo_llm_env=read_demo_llm_env(),
                    refresh_runtime_config=_refresh,
                    notify_config_invalidate=publish_config_invalidate,
                )
                await run_seed(deps)
        finally:
            await redis.shutdown()
    finally:
        await postgres.shutdown()


if __name__ == "__main__":
    from app.infrastructure.logging import setup_logging

    setup_logging()
    asyncio.run(main())
