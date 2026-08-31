"""HTTP API composition root and deterministic lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.application.services.runtime_policy_service import RuntimePolicyService
from app.composition.resources import (
    DEFAULT_RESOURCE_FACTORIES,
    ResourceFactories,
    open_process_resources,
)
from app.composition.shared import (
    RuntimePolicyRepositoryFactory,
    _default_runtime_policy_repository,
    build_shared_services,
)
from app.composition.tasks import RestartPolicy, TaskFailure, TaskKind, TaskSupervisor
from app.composition.types import ApiRuntime, RuntimeReadiness
from app.infrastructure.adapters.redis_capabilities import (
    RedisRuntimePolicyHintStreamFactory,
)
from app.infrastructure.external.runtime_policy_notifier import (
    RuntimePolicyHintListener,
    RuntimePolicyHintPublisher,
)
from app.infrastructure.security.cookie import AuthCookieManager
from app.infrastructure.security.csrf import CsrfService
from app.infrastructure.security.oauth_clients import OAuthClients
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings


@asynccontextmanager
async def open_api_runtime(
    settings: DeploymentSettings,
    *,
    factories: ResourceFactories = DEFAULT_RESOURCE_FACTORIES,
    runtime_policy_repository_factory: RuntimePolicyRepositoryFactory = (
        _default_runtime_policy_repository
    ),
    on_critical_failure: Callable[[TaskFailure], None] | None = None,
) -> AsyncIterator[ApiRuntime]:
    """Open the complete API graph without constructing kernel workers."""

    readiness = RuntimeReadiness()
    supervisor = TaskSupervisor(
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        on_critical_failure=on_critical_failure,
    )
    async with open_process_resources(
        settings,
        ProcessRole.API,
        factories=factories,
    ) as resources:
        try:
            shared = build_shared_services(
                resources,
                supervisor=supervisor,
                runtime_policy_repository_factory=runtime_policy_repository_factory,
            )
            await shared.runtime_policy_reader.initialize()

            runtime_policy_service = RuntimePolicyService(
                repository=shared.runtime_policy_repository,
                audit_service=shared.audit_service,
                hint_publisher=RuntimePolicyHintPublisher(redis=resources.general_redis),
            )
            cookie_manager = AuthCookieManager(
                domain=settings.cookie_domain,
                secure=settings.cookie_secure,
                access_max_age=settings.access_token_ttl_seconds,
                refresh_max_age=settings.refresh_token_ttl_seconds,
            )
            csrf_service = CsrfService()
            oauth_registry = OAuthClients(
                google_client_id=settings.google_client_id,
                google_client_secret=settings.google_client_secret,
                github_client_id=settings.github_client_id,
                github_client_secret=settings.github_client_secret,
            )
            if resources.redis_connectivity.available:
                policy_listener = RuntimePolicyHintListener(
                    streams=RedisRuntimePolicyHintStreamFactory(resources.general_redis),
                    reader=shared.runtime_policy_reader,
                )
                await supervisor.start(
                    "runtime-policy-hints",
                    policy_listener.run,
                    kind=TaskKind.AUXILIARY,
                    restart=RestartPolicy(),
                )
            runtime = ApiRuntime(
                settings=settings,
                resources=resources,
                readiness=readiness,
                supervisor=supervisor,
                uow_factory=shared.uow_factory,
                runtime_policy_repository=shared.runtime_policy_repository,
                runtime_policy_reader=shared.runtime_policy_reader,
                runtime_policy_service=runtime_policy_service,
                token_codec=shared.token_codec,
                secret_cipher=shared.secret_cipher,
                password_hasher=shared.password_hasher,
                service_api_key_hasher=shared.service_api_key_hasher,
                cookie_manager=cookie_manager,
                csrf_service=csrf_service,
                oauth_registry=oauth_registry,
                application_urls=shared.application_urls,
                rate_limit_store=shared.rate_limit_store,
                command_ingress=shared.command_ingress,
                run_admission_service=shared.run_admission_service,
                run_control_service=shared.run_control_service,
                run_projection=shared.run_projection,
                sandbox_factory=shared.sandbox_factory,
                object_storage=shared.object_storage,
                file_storage=shared.file_storage,
                session_streams=shared.session_streams,
                notification_streams=shared.notification_streams,
                auth_service=shared.auth_service,
                audit_service=shared.audit_service,
                usage_stats_service=shared.usage_stats_service,
                quota_service=shared.quota_service,
                mcp_integration_service=shared.mcp_integration_service,
                a2a_integration_service=shared.a2a_integration_service,
                inference_model_service=shared.inference_model_service,
                inference_endpoint_service=shared.inference_endpoint_service,
                inference_binding_service=shared.inference_binding_service,
                capability_service=shared.capability_service,
                inference_status_service=shared.inference_status_service,
                skill_service=shared.skill_service,
                team_service=shared.team_service,
                service_api_key_service=shared.service_api_key_service,
                memory_service=shared.memory_service,
                llm_token_usage_service=shared.llm_token_usage_service,
                status_service=shared.status_service,
                file_service=shared.file_service,
                session_service=shared.session_service,
                resource_binding_service=shared.resource_binding_service,
                agent_service=shared.agent_service,
                codebase_service=shared.codebase_service,
                knowledge_base_service=shared.knowledge_base_service,
                a2a_server_service=shared.a2a_server_service,
                artifact_service=shared.artifact_service,
                notification_service=shared.notification_service,
                scheduled_job_service=shared.scheduled_job_service,
                evidence_service=shared.evidence_service,
                patrol_pack_service=shared.patrol_pack_service,
                patrol_run_service=shared.patrol_run_service,
                patrol_evidence_service=shared.patrol_evidence_service,
                patrol_remediation_service=shared.patrol_remediation_service,
                compliance_service=shared.compliance_service,
                governance_profile_service=shared.governance_profile_service,
                governance_overview_service=shared.governance_overview_service,
            )
            readiness.mark_ready()
            yield runtime
        finally:
            readiness.mark_not_ready()
            await supervisor.stop()


__all__ = ["open_api_runtime"]
