"""Greenfield HTTP composition root grouped into four bounded contexts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.application.ports.crypto import ApplicationUrls
from app.composition.resources import (
    DEFAULT_RESOURCE_FACTORIES,
    ResourceFactories,
    open_process_resources,
)
from app.composition.tasks import TaskFailure, TaskSupervisor
from app.composition.types import ApiRuntime, RuntimeReadiness
from app.contexts.identity.auth import PostgresAuthService
from app.contexts.identity.operations import PostgresIdentityOperations
from app.contexts.identity.runtime import IdentityRuntime
from app.contexts.identity.services import PostgresGovernanceService, PostgresQuotaService
from app.contexts.inference.quota import PostgresQuotaGate
from app.contexts.inference.runtime import InferenceRuntime
from app.contexts.inference.services import OpenAIInferenceGateway, PostgresInferenceService
from app.contexts.inference.tooling import PostgresToolCatalog
from app.contexts.kernel.runtime import KernelApiRuntime
from app.contexts.knowledge.runtime import KnowledgeRuntime
from app.contexts.knowledge.services import PostgresKnowledgeService
from app.domain.runtime_policy.governance import GovernancePolicy
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.cookie import AuthCookieManager
from app.infrastructure.security.csrf import CsrfService
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.oauth_clients import OAuthClients
from app.infrastructure.security.password_hasher import PasswordHasher
from app.kernel.application.command_service import CommandService
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import Workflow
from app.kernel.domain.workflows.agent import agent_reducer
from app.kernel.domain.workflows.knowledge_ingest import knowledge_ingest_reducer
from app.kernel.infrastructure.postgres.facts import PostgresDecisionFactsFactory
from app.kernel.infrastructure.postgres.queries import (
    PostgresDispositionService,
    PostgresKernelQueryService,
)
from app.kernel.infrastructure.postgres.store import PostgresKernelStore
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings


@asynccontextmanager
async def open_api_runtime(
    settings: DeploymentSettings,
    *,
    factories: ResourceFactories = DEFAULT_RESOURCE_FACTORIES,
    on_critical_failure: Callable[[TaskFailure], None] | None = None,
) -> AsyncIterator[ApiRuntime]:
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
        session_factory = resources.postgres.session_factory
        cipher = ApiKeyCipher(
            settings.api_key_secret,
            key_id=settings.api_key_secret_id,
            previous_secrets=settings.api_key_previous_secrets,
        )

        def encrypt_private(value: dict[str, object]) -> str:
            return cipher.encrypt_versioned(
                json.dumps(value, sort_keys=True, separators=(",", ":"))
            )

        def decrypt_private(value: str) -> dict[str, object]:
            return json.loads(cipher.decrypt_versioned(value))

        quota_gate = PostgresQuotaGate(session_factory)
        commands = CommandService(
            store=PostgresKernelStore(
                session_factory,
                encrypt_private=encrypt_private,
                decrypt_private=decrypt_private,
                command_validator=quota_gate.validate_command,
            ),
            reducers=ReducerRegistry(
                {
                    Workflow.AGENT: agent_reducer,
                    Workflow.KNOWLEDGE_INGEST: knowledge_ingest_reducer,
                }
            ),
            facts_factory=PostgresDecisionFactsFactory(session_factory),
        )
        token_codec = JwtService(
            settings.jwt_secret,
            access_ttl_seconds=settings.access_token_ttl_seconds,
            refresh_ttl_seconds=settings.refresh_token_ttl_seconds,
            previous_secrets=settings.jwt_previous_secrets,
        )
        auth = PostgresAuthService(
            session_factory,
            password_hasher=PasswordHasher(),
            token_codec=token_codec,
            refresh_ttl_seconds=settings.refresh_token_ttl_seconds,
        )
        inference_service = PostgresInferenceService(
            session_factory,
            cipher=cipher,
        )
        inference_gateway = OpenAIInferenceGateway(
            session_factory,
            cipher=cipher,
        )
        identity_operations = PostgresIdentityOperations(
            session_factory,
            retention_days=GovernancePolicy().retention_days,
            storage=resources.object_storage_client,
        )
        identity = IdentityRuntime(
            commands=identity_operations,
            queries=identity_operations,
            transactions=session_factory,
            auth=auth,
            quotas=PostgresQuotaService(session_factory),
            governance=PostgresGovernanceService(session_factory),
            cookies=AuthCookieManager(
                domain=settings.cookie_domain,
                secure=settings.cookie_secure,
                access_max_age=settings.access_token_ttl_seconds,
                refresh_max_age=settings.refresh_token_ttl_seconds,
            ),
            csrf=CsrfService(),
            oauth=OAuthClients(
                google_client_id=settings.google_client_id,
                google_client_secret=settings.google_client_secret,
                github_client_id=settings.github_client_id,
                github_client_secret=settings.github_client_secret,
            ),
            application_urls=ApplicationUrls(
                frontend_base_url=settings.frontend_base_url,
                oauth_redirect_base=settings.oauth_redirect_base,
            ),
        )
        knowledge_service = PostgresKnowledgeService(
            session_factory,
            storage=resources.object_storage_client,
            commands=commands,
            retention_days=GovernancePolicy().retention_days,
            quota=quota_gate,
        )
        runtime = ApiRuntime(
            settings=settings,
            resources=resources,
            readiness=readiness,
            supervisor=supervisor,
            identity=identity,
            inference=InferenceRuntime(
                commands=inference_service,
                queries=inference_service,
                gateway=inference_gateway,
                transactions=session_factory,
            ),
            knowledge=KnowledgeRuntime(
                commands=knowledge_service,
                queries=knowledge_service,
                gateway=knowledge_service,
                transactions=session_factory,
                dispositions=knowledge_service,
            ),
            kernel=KernelApiRuntime(
                commands=commands,
                queries=PostgresKernelQueryService(session_factory),
                dispositions=PostgresDispositionService(
                    session_factory,
                    retention_days=GovernancePolicy().retention_days,
                ),
                catalog=PostgresToolCatalog(session_factory),
            ),
        )
        try:
            await auth.bootstrap_admin(
                email=settings.bootstrap_admin_email,
                password=settings.bootstrap_admin_password,
            )
            readiness.mark_ready()
            yield runtime
        finally:
            readiness.mark_not_ready()
            await supervisor.stop()


__all__ = ["open_api_runtime"]
