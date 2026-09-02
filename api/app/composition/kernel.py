"""Greenfield worker composition for Effects, timers, and retention only."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from app.composition.resources import (
    DEFAULT_RESOURCE_FACTORIES,
    ResourceFactories,
    open_process_resources,
)
from app.composition.tasks import TaskFailure, TaskSupervisor
from app.composition.types import KernelRuntime, RuntimeReadiness
from app.contexts.identity.operations import PostgresIdentityOperations
from app.contexts.identity.runtime import IdentityRuntime
from app.contexts.identity.services import PostgresGovernanceService, PostgresQuotaService
from app.contexts.inference.quota import PostgresQuotaGate
from app.contexts.inference.runtime import InferenceRuntime
from app.contexts.inference.services import OpenAIInferenceGateway, PostgresInferenceService
from app.contexts.inference.tooling import (
    PostgresToolGateway,
    SandboxFileGateway,
    SandboxRuntime,
)
from app.contexts.kernel.runtime import KernelWorkerRuntime
from app.contexts.knowledge.runtime import KnowledgeRuntime
from app.contexts.knowledge.services import PostgresKnowledgeService
from app.domain.runtime_policy.governance import GovernancePolicy
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.kernel.application.command_service import CommandService
from app.kernel.application.effect_worker import EffectWorker
from app.kernel.application.retained_effects import (
    FileEffect,
    GovernedToolEffect,
    KnowledgeBuildEffect,
    ModelCallEffect,
    RetrievalEffect,
    build_retained_effect_registry,
)
from app.kernel.application.retention_worker import RetentionWorker
from app.kernel.application.timer_worker import TimerWorker
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import Workflow
from app.kernel.domain.workflows.agent import agent_reducer
from app.kernel.domain.workflows.knowledge_ingest import knowledge_ingest_reducer
from app.kernel.infrastructure.postgres.claims import (
    PostgresEffectClaimStore,
    PostgresTimerClaimStore,
)
from app.kernel.infrastructure.postgres.facts import PostgresDecisionFactsFactory
from app.kernel.infrastructure.postgres.retention import PostgresRetentionStore
from app.kernel.infrastructure.postgres.store import PostgresKernelStore
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings


def _worker_id(kind: str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{kind}:{uuid4().hex[:8]}"


@asynccontextmanager
async def open_kernel_runtime(
    settings: DeploymentSettings,
    *,
    factories: ResourceFactories = DEFAULT_RESOURCE_FACTORIES,
    on_critical_failure: Callable[[TaskFailure], None] | None = None,
) -> AsyncIterator[KernelRuntime]:
    """Own the complete worker graph with no HTTP or retired feature services."""

    readiness = RuntimeReadiness()
    supervisor = TaskSupervisor(
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        on_critical_failure=on_critical_failure,
    )
    async with open_process_resources(
        settings,
        ProcessRole.EXECUTION_KERNEL,
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
                json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
            )

        def decrypt_private(value: str) -> dict[str, object]:
            return json.loads(cipher.decrypt_versioned(value))

        quota = PostgresQuotaGate(session_factory)
        commands = CommandService(
            store=PostgresKernelStore(
                session_factory,
                encrypt_private=encrypt_private,
                decrypt_private=decrypt_private,
                command_validator=quota.validate_command,
            ),
            reducers=ReducerRegistry(
                {
                    Workflow.AGENT: agent_reducer,
                    Workflow.KNOWLEDGE_INGEST: knowledge_ingest_reducer,
                }
            ),
            facts_factory=PostgresDecisionFactsFactory(session_factory),
        )
        inference_service = PostgresInferenceService(session_factory, cipher=cipher)
        inference_gateway = OpenAIInferenceGateway(session_factory, cipher=cipher)
        knowledge_service = PostgresKnowledgeService(
            session_factory,
            storage=resources.object_storage_client,
            commands=commands,
            retention_days=GovernancePolicy().retention_days,
            quota=quota,
        )
        sandbox = SandboxRuntime(settings)
        tool_gateway = PostgresToolGateway(
            session_factory,
            cipher=cipher,
            settings=settings,
            sandbox=sandbox,
        )
        handlers = build_retained_effect_registry(
            model=ModelCallEffect(quota=quota, inference=inference_gateway),
            retrieval=RetrievalEffect(knowledge=knowledge_service),
            tool=GovernedToolEffect(tools=tool_gateway),
            file=FileEffect(files=SandboxFileGateway(sandbox)),
            knowledge_build=KnowledgeBuildEffect(knowledge=knowledge_service),
        )
        identity_operations = PostgresIdentityOperations(
            session_factory,
            retention_days=GovernancePolicy().retention_days,
            storage=resources.object_storage_client,
        )
        runtime = KernelRuntime(
            settings=settings,
            resources=resources,
            readiness=readiness,
            supervisor=supervisor,
            identity=IdentityRuntime(
                commands=identity_operations,
                queries=identity_operations,
                transactions=session_factory,
                quotas=PostgresQuotaService(session_factory),
                governance=PostgresGovernanceService(session_factory),
            ),
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
            kernel=KernelWorkerRuntime(
                commands=commands,
                effects=EffectWorker(
                    store=PostgresEffectClaimStore(
                        session_factory,
                        decrypt_request=decrypt_private,
                    ),
                    handlers=handlers,
                    command_sink=commands,
                    worker_id=_worker_id("effects"),
                    batch_size=settings.execution_activity_batch_size,
                ),
                timers=TimerWorker(
                    store=PostgresTimerClaimStore(session_factory),
                    command_sink=commands,
                    worker_id=_worker_id("timers"),
                    batch_size=settings.execution_activity_batch_size,
                ),
                retention=RetentionWorker(
                    store=PostgresRetentionStore(session_factory),
                    command_sink=commands,
                    worker_id=_worker_id("retention"),
                    batch_size=min(settings.execution_activity_batch_size, 100),
                ),
            ),
        )
        try:
            readiness.mark_ready()
            yield runtime
        finally:
            readiness.mark_not_ready()
            await sandbox.close()
            await supervisor.stop()


__all__ = ["open_kernel_runtime"]
