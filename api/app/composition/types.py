"""Immutable values exchanged by process composition roots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.application.ports.coordination import RedisConnectivity
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from app.infrastructure.storage.postgres import Postgres
from app.infrastructure.storage.redis import RedisClient
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings

if TYPE_CHECKING:
    from app.application.execution.admission import RunAdmissionService
    from app.application.execution.command_ingress import CommandIngress
    from app.application.execution.run_control import RunControlService
    from app.application.ports.coordination import LeaseManagerPort, RateLimitStorePort
    from app.application.ports.crypto import (
        ApplicationUrls,
        CookieManagerPort,
        CsrfPort,
        OAuthRegistryPort,
        PasswordHashPort,
        ServiceKeyPort,
        TokenCodecPort,
        VersionedSecretCipher,
    )
    from app.application.ports.streams import (
        NotificationStreamFactory,
        SessionListStreamFactory,
        WakeupPort,
    )
    from app.application.services.a2a_server_service import A2AServerService
    from app.application.services.agent_service import AgentService
    from app.application.services.artifact_service import ArtifactService
    from app.application.services.audit_service import AuditService
    from app.application.services.auth_service import AuthService
    from app.application.services.capability_service import CapabilityService
    from app.application.services.compliance_service import ComplianceService
    from app.application.services.evidence_service import EvidenceService
    from app.application.services.file_service import FileService
    from app.application.services.governance_overview_service import GovernanceOverviewService
    from app.application.services.governance_profile_service import GovernanceProfileService
    from app.application.services.inference_binding_service import InferenceBindingService
    from app.application.services.inference_endpoint_service import InferenceEndpointService
    from app.application.services.inference_model_service import InferenceModelService
    from app.application.services.inference_status_service import InferenceStatusService
    from app.application.services.integration_server_service import (
        A2AIntegrationService,
        MCPServerService,
    )
    from app.application.services.knowledge_base_service import KnowledgeBaseService
    from app.application.services.llm_token_usage_service import LLMTokenUsageService
    from app.application.services.memory_service import MemoryService
    from app.application.services.notification_service import NotificationService
    from app.application.services.patrol_evidence_service import PatrolEvidenceService
    from app.application.services.patrol_pack_service import PatrolPackService
    from app.application.services.patrol_remediation_service import PatrolRemediationService
    from app.application.services.patrol_retention_service import PatrolRetentionService
    from app.application.services.patrol_run_service import PatrolRunService
    from app.application.services.quota_service import QuotaService
    from app.application.services.resource_binding_service import ResourceBindingService
    from app.application.services.resource_version_gc_service import ResourceVersionGCService
    from app.application.services.runtime_policy_reader import RuntimePolicyReader
    from app.application.services.runtime_policy_service import RuntimePolicyService
    from app.application.services.scheduled_job_service import ScheduledJobService
    from app.application.services.service_api_key_service import ServiceApiKeyService
    from app.application.services.session_service import SessionService
    from app.application.services.skill_service import SkillService
    from app.application.services.status_service import StatusService
    from app.application.services.team_service import TeamService
    from app.application.services.usage_stats_service import UsageStatsService
    from app.composition.tasks import TaskSupervisor
    from app.domain.external.file_storage import FileStorage
    from app.domain.external.object_storage import ObjectStoragePort
    from app.domain.external.sandbox import SandboxFactoryPort
    from app.domain.repositories.runtime_policy_repository import RuntimePolicyRepository
    from app.domain.repositories.uow import UnitOfWorkFactory
    from app.execution_kernel import ExecutionKernelRuntime
    from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
    from app.infrastructure.external.sandbox.sandbox_maintenance import SandboxMaintenance


class RuntimeReadiness:
    """Mutable lifecycle marker owned by an otherwise immutable runtime bundle."""

    def __init__(self) -> None:
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._ready = True

    def mark_not_ready(self) -> None:
        self._ready = False


@dataclass(frozen=True)
class ResourceBundle:
    """Process-owned infrastructure resources with one explicit lifetime."""

    settings: DeploymentSettings
    role: ProcessRole
    postgres: Postgres
    redis: RedisClient
    redis_connectivity: RedisConnectivity
    object_storage_client: Cos | Minio
    general_redis: Redis


@dataclass(frozen=True)
class ApiRuntime:
    """Complete HTTP-process graph; no execution-kernel workers are present."""

    settings: DeploymentSettings
    resources: ResourceBundle
    readiness: RuntimeReadiness
    supervisor: TaskSupervisor
    uow_factory: UnitOfWorkFactory
    runtime_policy_repository: RuntimePolicyRepository
    runtime_policy_reader: RuntimePolicyReader
    runtime_policy_service: RuntimePolicyService
    token_codec: TokenCodecPort
    secret_cipher: VersionedSecretCipher
    password_hasher: PasswordHashPort
    service_api_key_hasher: ServiceKeyPort
    cookie_manager: CookieManagerPort
    csrf_service: CsrfPort
    oauth_registry: OAuthRegistryPort
    application_urls: ApplicationUrls
    rate_limit_store: RateLimitStorePort
    command_ingress: CommandIngress
    run_admission_service: RunAdmissionService
    run_control_service: RunControlService
    run_projection: PostgresRunProjection
    sandbox_factory: SandboxFactoryPort
    object_storage: ObjectStoragePort
    file_storage: FileStorage
    session_streams: SessionListStreamFactory
    notification_streams: NotificationStreamFactory
    auth_service: AuthService
    audit_service: AuditService
    usage_stats_service: UsageStatsService
    quota_service: QuotaService
    mcp_integration_service: MCPServerService
    a2a_integration_service: A2AIntegrationService
    inference_model_service: InferenceModelService
    inference_endpoint_service: InferenceEndpointService
    inference_binding_service: InferenceBindingService
    capability_service: CapabilityService
    inference_status_service: InferenceStatusService
    skill_service: SkillService
    team_service: TeamService
    service_api_key_service: ServiceApiKeyService
    memory_service: MemoryService
    llm_token_usage_service: LLMTokenUsageService
    status_service: StatusService
    file_service: FileService
    session_service: SessionService
    resource_binding_service: ResourceBindingService
    agent_service: AgentService
    knowledge_base_service: KnowledgeBaseService
    a2a_server_service: A2AServerService
    artifact_service: ArtifactService
    notification_service: NotificationService
    scheduled_job_service: ScheduledJobService
    evidence_service: EvidenceService
    patrol_pack_service: PatrolPackService
    patrol_run_service: PatrolRunService
    patrol_evidence_service: PatrolEvidenceService
    patrol_remediation_service: PatrolRemediationService
    compliance_service: ComplianceService
    governance_profile_service: GovernanceProfileService
    governance_overview_service: GovernanceOverviewService


@dataclass(frozen=True)
class KernelRuntime:
    """Complete execution-kernel graph without HTTP presentation services."""

    settings: DeploymentSettings
    resources: ResourceBundle
    readiness: RuntimeReadiness
    supervisor: TaskSupervisor
    execution: ExecutionKernelRuntime
    policy_reader: RuntimePolicyReader
    wakeup: WakeupPort
    scheduler_leases: LeaseManagerPort
    uow_factory: UnitOfWorkFactory
    scheduler_service: ScheduledJobService
    resource_gc: ResourceVersionGCService
    patrol_retention: PatrolRetentionService
    sandbox_factory: SandboxFactoryPort
    sandbox_maintenance: SandboxMaintenance
