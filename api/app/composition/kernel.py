"""Execution-kernel composition root and deterministic lifecycle."""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial

from app.application.execution.activities.child_run import ChildRunActivityHandler
from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.activities.patrol import (
    PatrolExecutionActivityHandler,
    PatrolValidationActivityHandler,
)
from app.application.execution.activities.remediation import RemediationActivityHandler
from app.application.execution.activities.resource_build import KnowledgeBuildActivityHandler
from app.application.execution.activities.retrieval import RetrievalActivityHandler
from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.activity_registry import (
    ActivityRegistry,
    create_activity_registry,
)
from app.application.execution.agent_tool_catalog import AgentToolCatalog
from app.application.services.patrol_collector_validator import (
    MCPPatrolCollectorValidator,
)
from app.application.services.patrol_retention_service import PatrolRetentionService
from app.application.services.resource_version_gc_service import ResourceVersionGCService
from app.composition.resources import (
    DEFAULT_RESOURCE_FACTORIES,
    ResourceFactories,
    open_process_resources,
)
from app.composition.shared import (
    RuntimePolicyRepositoryFactory,
    SharedServices,
    _default_runtime_policy_repository,
    build_shared_services,
)
from app.composition.tasks import RestartPolicy, TaskFailure, TaskKind, TaskSupervisor
from app.composition.types import KernelRuntime, RuntimeReadiness
from app.domain.models.authorization import AuthorizationContext
from app.domain.services.knowledge_base.ingestion_runner import KBIngestionRunner
from app.infrastructure.adapters.execution_ports import build_execution_kernel_runtime
from app.infrastructure.adapters.query_ports import SqlAlchemyPatrolRetentionStore
from app.infrastructure.adapters.redis_capabilities import (
    RedisLeaseManager,
    RedisRuntimePolicyHintStreamFactory,
    RedisSandboxActivityStore,
    RedisWakeupAdapter,
)
from app.infrastructure.external.knowledge.web_connector import HttpWebDocumentGateway
from app.infrastructure.external.runtime_policy_notifier import RuntimePolicyHintListener
from app.infrastructure.external.sandbox.reclaim_coordinator import ReclaimCoordinator
from app.infrastructure.external.sandbox.sandbox_maintenance import SandboxMaintenance
from app.infrastructure.external.scheduler.job_scheduler import run_scheduler_loop
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings


def _worker_id(kind: str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{kind}:{uuid.uuid4().hex[:8]}"


def _build_activity_registry(shared: SharedServices) -> ActivityRegistry:
    tools = AgentToolCatalog(
        uow_factory=shared.uow_factory,
        sandbox_factory=shared.sandbox_factory,
        search_engine=shared.search_engine,
        mcp_connection_pool=shared.mcp_connection_pool,
        a2a_connection_pool=shared.a2a_connection_pool,
        mcp_servers=shared.mcp_integration_service,
        a2a_servers=shared.a2a_integration_service,
        file_storage=shared.file_storage,
        models=shared.inference_model_service,
        image_generator=shared.image_generator,
        artifacts=shared.artifact_service,
        memories=shared.memory_service,
        embeddings=shared.embedding_service,
        llm_factory=shared.resilient_llm_factory,
    )
    knowledge_pipeline = KBIngestionRunner(
        uow_factory=shared.uow_factory,
        file_storage=shared.file_storage,
        web_documents=HttpWebDocumentGateway(policy_reader=shared.runtime_policy_reader),
        json_parser=shared.json_parser,
        embeddings=shared.embedding_service,
    )
    collector = MCPPatrolCollectorValidator(shared.mcp_connection_pool)
    return create_activity_registry(
        ModelCallActivityHandler(
            objects=shared.activity_objects,
            models=shared.inference_model_service,
            tools=tools,
            skills=shared.skill_service,
            token_usage=shared.llm_token_usage_service,
            files=shared.file_service,
            client_factory=shared.resilient_llm_factory,
        ),
        RetrievalActivityHandler(
            objects=shared.activity_objects,
            tools=tools,
            memories=shared.memory_service,
        ),
        ToolCallActivityHandler(objects=shared.activity_objects, tools=tools),
        ChildRunActivityHandler(
            objects=shared.activity_objects,
            admission=shared.run_admission_service,
            runs=shared.run_projection,
        ),
        RemediationActivityHandler(
            objects=shared.activity_objects,
            executor=shared.patrol_remediation_service,
            policy_reader=shared.runtime_policy_reader,
        ),
        KnowledgeBuildActivityHandler(
            objects=shared.activity_objects,
            pipeline=knowledge_pipeline,
            models=shared.inference_model_service,
            client_factory=shared.resilient_llm_factory,
        ),
        PatrolExecutionActivityHandler(
            objects=shared.activity_objects,
            uow_factory=shared.uow_factory,
            collector=collector,
            runs=shared.patrol_run_service,
        ),
        PatrolValidationActivityHandler(
            objects=shared.activity_objects,
            uow_factory=shared.uow_factory,
            collector=collector,
            packs=shared.patrol_pack_service,
        ),
    )


@asynccontextmanager
async def open_kernel_runtime(
    settings: DeploymentSettings,
    *,
    factories: ResourceFactories = DEFAULT_RESOURCE_FACTORIES,
    runtime_policy_repository_factory: RuntimePolicyRepositoryFactory = (
        _default_runtime_policy_repository
    ),
    on_critical_failure: Callable[[TaskFailure], None] | None = None,
) -> AsyncIterator[KernelRuntime]:
    """Open the complete kernel graph without constructing HTTP presentation services."""

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
        try:
            shared = build_shared_services(
                resources,
                supervisor=supervisor,
                runtime_policy_repository_factory=runtime_policy_repository_factory,
            )
            await shared.runtime_policy_reader.initialize()

            redis = resources.general_redis
            leases = RedisLeaseManager(redis)
            activity_registry = _build_activity_registry(shared)
            execution = build_execution_kernel_runtime(
                session_factory=resources.postgres.session_factory,
                redis=redis,
                authorization=AuthorizationContext.system("execution-kernel"),
                activity_registry=activity_registry,
                worker_id=_worker_id("activities"),
            )
            resource_gc = ResourceVersionGCService(
                uow_factory=shared.uow_factory,
                policy_reader=shared.runtime_policy_reader,
            )
            patrol_retention = PatrolRetentionService(
                SqlAlchemyPatrolRetentionStore(resources.postgres.session_factory),
                policy_reader=shared.runtime_policy_reader,
            )
            sandbox_maintenance = SandboxMaintenance(
                factory=shared.sandbox_factory,
                reclaim=ReclaimCoordinator(
                    leases=leases,
                    worker_id=_worker_id("sandbox-reclaim"),
                ),
                activity_store=RedisSandboxActivityStore(redis),
            )

            if resources.redis_connectivity.available:
                policy_listener = RuntimePolicyHintListener(
                    streams=RedisRuntimePolicyHintStreamFactory(redis),
                    reader=shared.runtime_policy_reader,
                )
                await supervisor.start(
                    "runtime-policy-hints",
                    policy_listener.run,
                    kind=TaskKind.AUXILIARY,
                    restart=RestartPolicy(),
                )

            runtime = KernelRuntime(
                settings=settings,
                resources=resources,
                readiness=readiness,
                supervisor=supervisor,
                execution=execution,
                policy_reader=shared.runtime_policy_reader,
                wakeup=RedisWakeupAdapter(redis),
                scheduler_leases=leases,
                uow_factory=shared.uow_factory,
                scheduler_service=shared.scheduled_job_service,
                resource_gc=resource_gc,
                patrol_retention=patrol_retention,
                sandbox_factory=shared.sandbox_factory,
                sandbox_maintenance=sandbox_maintenance,
            )
            await supervisor.start(
                "scheduler",
                partial(
                    run_scheduler_loop,
                    shared.uow_factory,
                    shared.scheduled_job_service,
                    leases=leases,
                    worker_id=_worker_id("scheduler"),
                    policy_reader=shared.runtime_policy_reader,
                    stop_event=supervisor.stop_event,
                    resource_version_gc_service=resource_gc,
                    patrol_retention_service=patrol_retention,
                    mcp_pool=shared.mcp_connection_pool,
                    a2a_pool=shared.a2a_connection_pool,
                ),
                kind=TaskKind.CRITICAL,
            )
            if shared.sandbox_factory.deployment.address is None:
                await supervisor.start(
                    "sandbox-pool",
                    partial(
                        shared.sandbox_factory.pool.run,
                        supervisor.stop_event,
                    ),
                    kind=TaskKind.CRITICAL,
                )
                await supervisor.start(
                    "sandbox-maintenance",
                    partial(sandbox_maintenance.run, supervisor.stop_event),
                    kind=TaskKind.CRITICAL,
                )
            readiness.mark_ready()
            yield runtime
        finally:
            readiness.mark_not_ready()
            await supervisor.stop()


__all__ = ["open_kernel_runtime"]
