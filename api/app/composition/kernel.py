"""Execution-kernel composition root and deterministic lifecycle."""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial

from app.application.evaluation.judge_runtime import JudgeRuntime
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
from app.application.execution.decisions import validate_decision_registry
from app.application.services.execution_queue_retention_service import (
    ExecutionQueueRetentionService,
)
from app.application.services.patrol_collector_validator import (
    MCPPatrolCollectorValidator,
)
from app.application.services.patrol_retention_service import PatrolRetentionService
from app.application.services.recycle_bin_retention_service import (
    RecycleBinRetentionService,
)
from app.application.services.resource_version_gc_service import ResourceVersionGCService
from app.composition.evaluation import (
    build_batch_scheduler,
    build_environment_runtime,
    build_recording_service,
    build_replay_runtime,
)
from app.composition.evaluation_execution import build_evaluation_execution
from app.composition.execution_comparison import build_comparison_diff_worker
from app.composition.execution_export import build_export_worker
from app.composition.kernel_runtime import build_execution_kernel_runtime
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
from app.infrastructure.adapters.execution_ports import (
    SqlAlchemyExecutionQueueRetentionStore,
)
from app.infrastructure.adapters.query_ports import SqlAlchemyPatrolRetentionStore
from app.infrastructure.adapters.redis_capabilities import (
    RedisLeaseManager,
    RedisRuntimePolicyHintStreamFactory,
    RedisSandboxActivityStore,
    RedisWakeupAdapter,
)
from app.infrastructure.execution.postgres_artifact_provenance import ArtifactProvenanceMaintenance
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.execution.postgres_recovery import PostgresRecoveryWorker
from app.infrastructure.external.knowledge.web_connector import HttpWebDocumentGateway
from app.infrastructure.external.runtime_policy_notifier import RuntimePolicyHintListener
from app.infrastructure.external.sandbox.factory import PooledSandboxFactory
from app.infrastructure.external.sandbox.reclaim_coordinator import ReclaimCoordinator
from app.infrastructure.external.sandbox.sandbox_maintenance import SandboxMaintenance
from app.infrastructure.external.scheduler.job_scheduler import (
    run_maintenance_loop,
    run_scheduler_loop,
)
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings


def _worker_id(kind: str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{kind}:{uuid.uuid4().hex[:8]}"


def _build_activity_registry(
    shared: SharedServices, *, replay=None, isolated=None, text_stream=False
) -> ActivityRegistry:
    from app.application.evaluation.contract_capture import ContractCapture

    tools = AgentToolCatalog(
        replay=replay,
        isolated=isolated,
        contract_capture=ContractCapture(shared.uow_factory),
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
            execution_usage=shared.execution_usage_service,
            files=shared.file_service,
            client_factory=shared.resilient_llm_factory,
            quota=shared.quota_service,
            judge=JudgeRuntime(shared.uow_factory),
            text_stream=text_stream,
        ),
        RetrievalActivityHandler(
            execution_usage=shared.execution_usage_service,
            objects=shared.activity_objects,
            tools=tools,
            memories=shared.memory_service,
            replay=replay,
            isolated=isolated,
        ),
        ToolCallActivityHandler(
            objects=shared.activity_objects,
            tools=tools,
            replay=replay,
            execution_usage=shared.execution_usage_service,
        ),
        ChildRunActivityHandler(
            objects=shared.activity_objects,
            admission=shared.run_admission_service,
            runs=shared.run_projection,
        ),
        RemediationActivityHandler(
            objects=shared.activity_objects,
            executor=shared.patrol_remediation_service,
            isolated=isolated,
            policy_reader=shared.runtime_policy_reader,
        ),
        KnowledgeBuildActivityHandler(
            execution_usage=shared.execution_usage_service,
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
    object_storage_wrapper=None,
    text_stream: bool = False,
    progress_sink_wrapper=None,
    broker_request_observer=None,
    shutdown_observer=None,
) -> AsyncIterator[KernelRuntime]:
    """Open the complete kernel graph without constructing HTTP presentation services."""

    export_worker = None
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
                object_storage_wrapper=object_storage_wrapper,
            )
            await shared.runtime_policy_reader.initialize()
            from app.composition.physical_budget import initialize_physical_policy

            await initialize_physical_policy(
                settings=settings, session_factory=resources.postgres.session_factory
            )

            from app.composition.environment_capacity import initialize_environment_capacity

            await initialize_environment_capacity(
                settings=settings, session_factory=resources.postgres.session_factory
            )

            redis = resources.general_redis
            leases = RedisLeaseManager(redis)
            activity_registry = _build_activity_registry(
                shared,
                text_stream=text_stream,
                replay=build_replay_runtime(settings=settings, resources=resources, shared=shared),
                isolated=build_environment_runtime(
                    settings=settings, shared=shared, request_observer=broker_request_observer
                ),
            )
            # 启动自检（D10）：决策侧声明的 activity 类型必须全部有已注册 handler。
            validate_decision_registry(activity_registry.registered_types)

            async def _approval_ttl_minutes(now):
                active = await shared.runtime_policy_reader.active_operations(
                    require_fresh=False,
                    now=now,
                )
                return active.revision.policy.approval.ttl_minutes

            from app.application.execution.activity_inputs import ActivityObjectStore
            from app.infrastructure.execution.postgres_execution_content import (
                ExecutionContentWriter,
            )

            content_writer = ExecutionContentWriter(
                session_factory=resources.postgres.session_factory,
                authorization=AuthorizationContext.system("execution-kernel"),
                objects=ActivityObjectStore(shared.object_storage),
            )
            evaluation_execution = await build_evaluation_execution(
                settings=settings,
                session_factory=resources.postgres.session_factory,
            )
            execution = build_execution_kernel_runtime(
                evaluation_execution=evaluation_execution,
                progress_sink_wrapper=progress_sink_wrapper,
                content_writer=content_writer,
                session_factory=resources.postgres.session_factory,
                redis=redis,
                authorization=AuthorizationContext.system("execution-kernel"),
                activity_registry=activity_registry,
                worker_id=_worker_id("activities"),
                activity_max_concurrency=settings.execution_activity_max_concurrency,
                activity_max_claim_attempts=settings.execution_activity_max_claim_attempts,
                inbox_max_claim_attempts=settings.execution_inbox_max_claim_attempts,
                approval_ttl_minutes=_approval_ttl_minutes,
            )
            resource_gc = ResourceVersionGCService(
                uow_factory=shared.uow_factory,
                policy_reader=shared.runtime_policy_reader,
            )
            patrol_retention = PatrolRetentionService(
                SqlAlchemyPatrolRetentionStore(resources.postgres.session_factory),
                policy_reader=shared.runtime_policy_reader,
            )
            recycle_bin_retention = RecycleBinRetentionService(
                uow_factory=shared.uow_factory,
                retention_days=settings.recycle_bin_retention_days,
                batch_size=settings.recycle_bin_purge_batch_size,
                audit_service=shared.audit_service,
            )
            execution_queue_retention = ExecutionQueueRetentionService(
                SqlAlchemyExecutionQueueRetentionStore(
                    session_factory=resources.postgres.session_factory,
                    authorization=AuthorizationContext.system("execution-kernel"),
                ),
                inbox_retention_days=settings.execution_inbox_retention_days,
                inbox_dead_letter_retention_days=(
                    settings.execution_inbox_dead_letter_retention_days
                ),
                outbox_retention_days=settings.execution_outbox_retention_days,
                timer_retention_days=settings.execution_timer_retention_days,
                activity_retention_days=settings.execution_activity_retention_days,
                batch_size=settings.execution_queue_purge_batch_size,
            )
            from app.infrastructure.execution.postgres_execution_usage import (
                ExecutionUsageMaintenance,
            )

            usage_maintenance = ExecutionUsageMaintenance(
                session_factory=resources.postgres.session_factory,
                authorization=AuthorizationContext.system("execution-kernel"),
                handler=execution,
            )
            from app.infrastructure.repositories.db_evaluation_dataset_repository import (
                DatasetObjectLifecycle,
            )

            dataset_objects = DatasetObjectLifecycle(
                resources.postgres.upload_intent_session_factory,
                shared.object_storage,
                signing_secret=settings.database_authorization_signing_secret,
            )
            from app.composition.evaluation import build_environment_registry
            from app.infrastructure.repositories.environment_maintenance import (
                EnvironmentMaintenance,
            )
            from app.infrastructure.repositories.recording_maintenance import RecordingMaintenance
            from app.infrastructure.repositories.recording_object_lifecycle import (
                RecordingObjectLifecycle,
            )

            environment_worker = EnvironmentMaintenance(
                shared.uow_factory,
                build_environment_registry(settings, request_observer=broker_request_observer),
            )
            recording_objects = RecordingObjectLifecycle(
                resources.postgres.upload_intent_session_factory,
                shared.object_storage,
                signing_secret=settings.database_authorization_signing_secret,
            )
            recording_worker = RecordingMaintenance(
                shared.uow_factory,
                lambda authorization: build_recording_service(
                    settings=settings,
                    resources=resources,
                    shared=shared,
                    authorization=authorization,
                ),
                recording_objects,
            )
            artifact_maintenance = ArtifactProvenanceMaintenance(
                session_factory=resources.postgres.session_factory,
                authorization=AuthorizationContext.system("execution-kernel"),
                objects=shared.object_storage,
                handler=execution,
            )
            recovery_worker = PostgresRecoveryWorker(
                session_factory=resources.postgres.session_factory,
                authorization=AuthorizationContext.system("execution-kernel"),
                audit_signing_key=settings.audit_signing_key,
                audit_signing_key_id=settings.audit_signing_key_id,
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

            from app.application.evaluation.runtime import EvaluationRuntime
            from app.composition.evaluation import (
                build_judge_service,
                build_review_command_consumer,
                build_rule_scoring_service,
            )
            from app.infrastructure.evaluation.runtime_inventory import EvaluationRuntimeInventory

            inventory = EvaluationRuntimeInventory(shared.uow_factory)
            evaluation = EvaluationRuntime(
                scheduler=build_batch_scheduler(
                    settings=settings, resources=resources, shared=shared
                ),
                rules=build_rule_scoring_service(
                    settings=settings, resources=resources, shared=shared
                ),
                judge=build_judge_service(settings=settings, resources=resources, shared=shared),
                reviews=build_review_command_consumer(
                    settings=settings, resources=resources, shared=shared
                ),
                discover=inventory.discover,
                cleanup=(
                    recording_worker.process_pending,
                    environment_worker.process_pending,
                    dataset_objects.cleanup,
                    recording_objects.cleanup,
                    inventory.cleanup_summary,
                ),
            )
            for name, action in (
                ("evaluation-scheduler", evaluation.schedule),
                ("evaluation-reconciler", evaluation.reconcile),
                ("evaluation-scoring", evaluation.score),
                ("evaluation-cleanup", evaluation.clean),
            ):
                await supervisor.start(
                    name,
                    partial(
                        evaluation.run,
                        action,
                        stop_event=supervisor.stop_event,
                        interval_seconds=settings.evaluation_poll_interval_seconds,
                    ),
                    kind=TaskKind.CRITICAL,
                )

            runtime = KernelRuntime(
                evaluation_runtime=evaluation,
                evaluation_scheduler=evaluation.scheduler,
                settings=settings,
                resources=resources,
                readiness=readiness,
                supervisor=supervisor,
                execution=execution,
                policy_reader=shared.runtime_policy_reader,
                wakeup=RedisWakeupAdapter(redis, consumer_name=_worker_id("wakeup")),
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
                    recycle_bin_retention_service=recycle_bin_retention,
                    execution_queue_retention_service=execution_queue_retention,
                    mcp_pool=shared.mcp_connection_pool,
                    a2a_pool=shared.a2a_connection_pool,
                ),
                kind=TaskKind.CRITICAL,
            )
            export_worker, export_cleanup = build_export_worker(
                settings=settings, resources=resources, shared=shared
            )
            comparison_diff_worker = build_comparison_diff_worker(
                settings=settings, resources=resources, shared=shared
            )
            for name, action in (
                ("execution-export", export_worker.process_pending),
                ("execution-export-cleanup", export_cleanup),
                ("comparison-artifact-diff", comparison_diff_worker.process_pending),
                ("notification-delivery", shared.notification_service.process_deliveries),
                ("patrol-recheck", shared.patrol_remediation_service.reconcile_rechecks),
                ("execution-recovery", recovery_worker.process_pending),
                ("artifact-provenance", artifact_maintenance.process_pending),
                ("execution-usage", usage_maintenance.process_pending),
                (
                    "execution-view-cache",
                    PostgresExecutionView(
                        session_factory=resources.postgres.session_factory,
                        authorization=AuthorizationContext.system("execution-kernel"),
                    ).cleanup_expired,
                ),
            ):
                await supervisor.start(
                    name,
                    partial(run_maintenance_loop, action, stop_event=supervisor.stop_event),
                    kind=TaskKind.CRITICAL,
                )
            # The kernel graph always builds the pooled factory (P2-16②);
            # the isinstance check narrows the shared field's base type and
            # guards against a future wiring regression.
            if not isinstance(shared.sandbox_factory, PooledSandboxFactory):
                raise TypeError("execution kernel requires a PooledSandboxFactory")
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
            try:
                reports = await supervisor.stop()
                if shutdown_observer is not None:
                    shutdown_observer(reports)
            finally:
                if export_worker is not None:
                    await export_worker.close()


__all__ = ["open_kernel_runtime"]
