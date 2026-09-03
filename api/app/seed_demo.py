"""Seed a runnable Ops Patrol demonstration.

Container entrypoint:  docker compose exec opencitadel-api python -m app.seed_demo

Automates the manual steps from docs/tutorials/06-ops-patrol.md end to end:

1. Enable the ``ops-collector`` MCP server and persist its nine fixed
   read-only Tool Policies (values from docs/operations/ops-patrol.md).
2. Optionally register a demo inference endpoint/model/binding from
   DEMO_INFERENCE_BASE_URL/DEMO_INFERENCE_CREDENTIAL/DEMO_INFERENCE_MODEL/
   DEMO_INFERENCE_PROVIDER env
   vars, and create the global chat-purpose binding.
3. Create a draft custom "Demo Governance Patrol" Pack with three checks.
   Live validation and activation run later through the execution kernel.
4. Print demo guidance, including how to manufacture a Finding on demand.

Every step is idempotent: re-running this module makes zero additional writes
once the demo state exists.

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
  WARNING severity. It still becomes a Finding, but the Pack's assertion and
  `severity_on_fail` are not evaluated.

For a demo whose "stop a container -> see a Finding" story must be reliable
and easy to explain, `dependency_status`/TCP is the better choice. This
seed's third check therefore targets `demo-console-tcp`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from app.application.ports.crypto import OutboundNetworkPolicy
from app.application.ports.reporting import AuditVerificationKeyring
from app.application.security.authorization_context import authorization_scope
from app.application.services.audit_service import AuditService
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_endpoint_service import InferenceEndpointService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.integration_server_service import MCPServerService
from app.application.services.patrol_pack_service import PatrolPackService
from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.inference import (
    InferenceEndpoint,
    InferenceModel,
    InferenceProvider,
    InferencePurpose,
    ResourceVisibility,
)
from app.domain.models.integration_runtime import MCPTransport
from app.domain.models.integration_server import MCPServerRecord
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
from app.domain.utils.outbound_url import parse_allowed_ports
from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
from app.infrastructure.adapters.query_ports import SqlAlchemyAuditSummaryQuery
from app.infrastructure.adapters.reporting_ports import PrometheusGovernanceMetricsAdapter
from app.infrastructure.adapters.security_ports import (
    FernetSecretEnvelopeAdapter,
    FernetVersionedSecretCipherAdapter,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.storage.postgres import Postgres
from core.config import DeploymentSettings, load_deployment_settings

logger = logging.getLogger(__name__)

DEMO_MCP_SERVER_NAME = "ops-collector"
DEMO_MCP_SERVER_ID = "demo-ops-collector"
DEMO_MCP_SERVER_URL = "http://opencitadel-ops-collector:8090/mcp"
DEMO_INFERENCE_ENDPOINT_NAME = "Demo Endpoint"
DEMO_INFERENCE_MODEL_NAME = "Demo Model"
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
# http_probe). The execution-kernel validation Run re-verifies this against
# the live Collector's get_capabilities() before the Pack may activate, so a
# stale constant here fails closed rather than silently activating.
DEMO_OUTPUT_SCHEMA_HASH = "03af4b9122da051ecb5e5d707552f27b09875d46b1124b671bb350fad06b28e1"

DEMO_TARGET_REF = "opencitadel-local"
DEMO_CLUSTER = "opencitadel-demo"
DEMO_NAMESPACE = "opencitadel"
DEMO_ENVIRONMENT = "dev"


def _desired_tool_policies() -> dict[str, ToolExecutionPolicy]:
    tool_names = sorted(PATROL_PROBE_TOOLS | {"get_capabilities"})
    return dict.fromkeys(tool_names, DEMO_TOOL_POLICY)


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
class DemoInferenceEnv:
    base_url: str
    credential: str
    model: str
    provider: str = "openai"


def read_demo_inference_env() -> DemoInferenceEnv | None:
    base_url = os.environ.get("DEMO_INFERENCE_BASE_URL", "").strip()
    credential = os.environ.get("DEMO_INFERENCE_CREDENTIAL", "").strip()
    model = os.environ.get("DEMO_INFERENCE_MODEL", "").strip()
    provider = os.environ.get("DEMO_INFERENCE_PROVIDER", "openai").strip() or "openai"
    if not (base_url and credential and model):
        return None
    return DemoInferenceEnv(
        base_url=base_url,
        credential=credential,
        model=model,
        provider=provider,
    )


@dataclass
class SeedDeps:
    mcp_server_service: MCPServerService
    inference_endpoint_service: InferenceEndpointService
    inference_model_service: InferenceModelService
    inference_binding_service: InferenceBindingService
    patrol_pack_service: PatrolPackService
    admin_user_id: str
    demo_inference_env: DemoInferenceEnv | None = None
    ops_collector_token: str = ""


def _collector_auth_headers(
    token: str, existing: dict[str, str] | None = None
) -> dict[str, str] | None:
    """Merge the ``Authorization: Bearer`` header the Collector now requires.

    ops-collector rejects unauthenticated streamable-http requests, so the
    seeded MCPServerRecord must carry the shared token. Empty token leaves the
    existing headers untouched (legacy / stdio deployments)."""
    cleaned = token.strip()
    if not cleaned:
        return existing
    merged = dict(existing or {})
    merged["Authorization"] = f"Bearer {cleaned}"
    return merged


async def seed_mcp_tool_policies(
    mcp_server_service: MCPServerService,
    *,
    actor_user_id: str,
    collector_token: str = "",
) -> str:
    """Enable ops-collector and persist its nine tool policies.

    Uses the same validated service path as the documented management API.
    """
    servers = await mcp_server_service.list_servers(mask=False)
    target = next((server for server in servers if server.name == DEMO_MCP_SERVER_NAME), None)
    if target is None:
        await mcp_server_service.create_server(
            MCPServerRecord(
                id=DEMO_MCP_SERVER_ID,
                name=DEMO_MCP_SERVER_NAME,
                transport=MCPTransport.STREAMABLE_HTTP,
                url=DEMO_MCP_SERVER_URL,
                enabled=True,
                headers=_collector_auth_headers(collector_token),
                tool_policies=_desired_tool_policies(),
                visibility=ResourceVisibility.GLOBAL,
            ),
            actor_user_id=actor_user_id,
            is_admin=True,
        )
        return "create"
    desired = _desired_tool_policies()
    desired_headers = _collector_auth_headers(collector_token, target.headers)
    if target.enabled and target.tool_policies == desired and target.headers == desired_headers:
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
        headers=desired_headers,
        env=target.env,
        transport_options=target.transport_options,
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


async def seed_inference(
    endpoint_service: InferenceEndpointService,
    model_service: InferenceModelService,
    binding_service: InferenceBindingService,
    env: DemoInferenceEnv | None,
) -> str:
    if env is None:
        return "skip-no-env"
    endpoints = await endpoint_service.list_endpoints(scope=None)
    existing = next(
        (item for item in endpoints if item.display_name == DEMO_INFERENCE_ENDPOINT_NAME),
        None,
    )
    if existing is not None:
        return "skip"
    endpoint = InferenceEndpoint(
        display_name=DEMO_INFERENCE_ENDPOINT_NAME,
        provider=InferenceProvider(env.provider),
        base_url=env.base_url,
        credential=env.credential,
        visibility=ResourceVisibility.GLOBAL,
    )
    created_endpoint = await endpoint_service.create_endpoint(
        endpoint,
        scope=None,
        allow_global_mutation=True,
    )
    model = InferenceModel(
        endpoint_id=created_endpoint.id,
        display_name=DEMO_INFERENCE_MODEL_NAME,
        model_name=env.model,
        visibility=ResourceVisibility.GLOBAL,
    )
    created_model = await model_service.create_model(
        model,
        scope=None,
        allow_global_mutation=True,
    )
    await binding_service.set_binding(
        InferencePurpose.CHAT,
        created_model.id,
        scope=None,
    )
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
        raise RuntimeError(
            f"MCP server '{DEMO_MCP_SERVER_NAME}' not found; run seed_mcp_tool_policies() first"
        )

    await patrol_pack_service.create_pack(
        owner_user_id=owner_user_id,
        scope=scope,
        name=DEMO_PACK_NAME,
        mcp_server_id=collector.id,
        config=build_demo_pack_config(),
        slug=DEMO_PACK_SLUG,
    )
    return "create"


def _log(step: str, outcome: str) -> None:
    prefix = "[skip]" if outcome.startswith("skip") else "[create]"
    print(f"{prefix} {step}: {outcome}")


def _print_demo_guide(results: dict[str, str]) -> None:
    print()
    print("=" * 72)
    print("OpenCitadel demo data is ready.")
    print("=" * 72)
    print(f"  1. Log in, open 'Ops Patrol', and select '{DEMO_PACK_NAME}'.")
    print("  2. Validate and activate the draft Pack; live Collector access runs")
    print("     as a formal execution-kernel validation Run.")
    print("  3. Click 'Run now' and wait for the Run to reach a terminal state.")
    print("  4. Open /admin/governance for the governance overview dashboard.")
    print()
    print("To manufacture a Finding on demand:")
    print("  docker compose stop ops-console")
    print("  ...then Run now again. The 'demo-console-health' check (a")
    print("  dependency_status probe against demo-console-tcp) fails")
    print("  deterministically: the refused TCP dial still comes back as")
    print('  envelope status="ok" with data.healthy=false, and the')
    print("  server-side assertion turns that into a FAIL -> Finding.")
    print("  (An http_probe against a fully-stopped target instead returns")
    print('  status="unavailable", which skips assertion evaluation')
    print("  entirely and is less demo-friendly — see api/app/seed_demo.py")
    print("  module docstring for the full read-the-code justification.)")
    print("  docker compose start ops-console   # restore before the next demo")
    print()
    if results.get("inference") == "skip-no-env":
        print("No demo inference model was registered (DEMO_INFERENCE_BASE_URL/")
        print("DEMO_INFERENCE_CREDENTIAL/DEMO_INFERENCE_MODEL not set).")
    print("=" * 72)


async def run_seed(deps: SeedDeps) -> dict[str, str]:
    results: dict[str, str] = {}

    results["mcp_tool_policies"] = await seed_mcp_tool_policies(
        deps.mcp_server_service,
        actor_user_id=deps.admin_user_id,
        collector_token=deps.ops_collector_token,
    )
    _log("MCP server 'ops-collector' enabled + 9 tool policies", results["mcp_tool_policies"])

    results["inference"] = await seed_inference(
        deps.inference_endpoint_service,
        deps.inference_model_service,
        deps.inference_binding_service,
        deps.demo_inference_env,
    )
    _log("Demo inference endpoint/model/binding", results["inference"])

    results["demo_pack"] = await seed_demo_pack(
        deps.patrol_pack_service,
        deps.mcp_server_service,
        owner_user_id=deps.admin_user_id,
    )
    _log(f"Patrol pack '{DEMO_PACK_NAME}' ({DEMO_PACK_SLUG})", results["demo_pack"])

    _print_demo_guide(results)
    return results


async def run_seed_command(
    settings: DeploymentSettings,
    *,
    postgres_factory: Callable[[DeploymentSettings], Postgres] = Postgres,
) -> dict[str, str]:
    """Open only PostgreSQL and execute the idempotent demo seed."""

    postgres = postgres_factory(settings)
    await postgres.init()
    try:
        with authorization_scope(AuthorizationContext.system("seed-demo")):
            cipher = ApiKeyCipher(
                settings.api_key_secret,
                key_id=settings.api_key_secret_id,
                previous_secrets=settings.api_key_previous_secrets,
            )

            versioned_cipher = FernetVersionedSecretCipherAdapter(cipher)
            uow_factory = create_uow_factory(
                session_factory=postgres.session_factory,
                dependencies=DBUnitOfWorkDependencies(
                    secret_cipher=versioned_cipher,
                    audit_signing_key=settings.audit_signing_key,
                    audit_signing_key_id=settings.audit_signing_key_id,
                    database_authorization_signing_secret=(
                        settings.database_authorization_signing_secret
                    ),
                ),
            )

            governance_metrics = PrometheusGovernanceMetricsAdapter()
            audit_service = AuditService(
                uow_factory,
                AuditVerificationKeyring(
                    keys={
                        settings.audit_signing_key_id: (settings.audit_signing_key,),
                        **{
                            str(key_id): (str(secret),)
                            for key_id, secret in settings.audit_previous_signing_keys.items()
                        },
                    }
                ),
                governance_metrics,
                SqlAlchemyAuditSummaryQuery(postgres.session_factory),
            )
            outbound_policy = OutboundNetworkPolicy(
                allowed_ports=parse_allowed_ports(settings.outbound_allowed_ports),
                allow_private_hosts=frozenset(
                    item.strip()
                    for item in settings.outbound_private_host_allowlist.split(",")
                    if item.strip()
                ),
            )
            provider_adapter = InfrastructureInferenceProviderAdapter(
                outbound_policy=outbound_policy,
            )
            mcp_server_service = MCPServerService(
                uow_factory,
                FernetSecretEnvelopeAdapter(cipher),
                outbound_policy,
                audit_service,
            )
            patrol_pack_service = PatrolPackService(uow_factory, audit_service)
            inference_model_service = InferenceModelService(
                uow_factory,
                provider_adapter,
                provider_adapter,
                provider_adapter,
            )
            inference_endpoint_service = InferenceEndpointService(
                uow_factory,
                versioned_cipher,
                outbound_policy,
                provider_adapter,
            )
            inference_binding_service = InferenceBindingService(uow_factory, provider_adapter)
            async with uow_factory() as uow:
                admin = await uow.user.get_by_email(settings.bootstrap_admin_email)
            if admin is None:
                raise SystemExit(
                    f"Bootstrap admin user '{settings.bootstrap_admin_email}' not found. "
                    "Start the API once first so bootstrap_admin_user() seeds it, then re-run."
                )

            deps = SeedDeps(
                mcp_server_service=mcp_server_service,
                inference_endpoint_service=inference_endpoint_service,
                inference_model_service=inference_model_service,
                inference_binding_service=inference_binding_service,
                patrol_pack_service=patrol_pack_service,
                admin_user_id=admin.id,
                demo_inference_env=read_demo_inference_env(),
                ops_collector_token=settings.ops_collector_token,
            )
            return await run_seed(deps)
    finally:
        await postgres.shutdown()


def main() -> None:
    settings = load_deployment_settings()
    from app.infrastructure.logging import setup_logging

    setup_logging(settings)
    asyncio.run(run_seed_command(settings))


if __name__ == "__main__":
    main()
