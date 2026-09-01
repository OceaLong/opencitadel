"""Ordinary-constructor assembly shared by process-specific composition roots."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.admission import RunAdmissionService
from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import PublicEventCursor
from app.application.execution.run_control import RunControlService
from app.application.ports.crypto import (
    ApplicationUrls,
    OutboundNetworkPolicy,
    PasswordHashPort,
    ServiceKeyPort,
    TokenCodecPort,
    VersionedSecretCipher,
)
from app.application.ports.reporting import (
    AuditVerificationKeyring,
    ComplianceRuntimeValues,
)
from app.application.services.a2a_server_service import A2AServerService
from app.application.services.agent_service import AgentService
from app.application.services.artifact_service import ArtifactService
from app.application.services.audit_service import AuditService
from app.application.services.auth_service import AuthService
from app.application.services.capability_service import CapabilityService
from app.application.services.compliance_service import (
    ADMIN_AUDIT_ACTION_PREFIX,
    EVIDENCE_EXPORT_AUDIT_ACTION,
    LOGIN_AUDIT_ACTIONS,
    ComplianceService,
)
from app.application.services.embedding_service import EmbeddingService
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
from app.application.services.knowledge_version_service import KnowledgeVersionService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.memory_service import MemoryService
from app.application.services.notification_service import NotificationService
from app.application.services.patrol_evidence_service import PatrolEvidenceService
from app.application.services.patrol_pack_service import PatrolPackService
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.application.services.patrol_run_service import PatrolRunService
from app.application.services.quota_service import QuotaService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.application.services.runtime_policy_reader import RuntimePolicyReader
from app.application.services.scheduled_job_service import ScheduledJobService
from app.application.services.service_api_key_service import ServiceApiKeyService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageStatsService
from app.composition.tasks import TaskSupervisor
from app.composition.types import ResourceBundle
from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.health_status import HealthStatus
from app.domain.repositories.runtime_policy_repository import RuntimePolicyRepository
from app.domain.repositories.uow import UnitOfWorkFactory
from app.domain.services.resource_version_provider import ResourceVersionProviderRegistry
from app.domain.utils.outbound_url import parse_allowed_ports
from app.infrastructure.adapters.connection_pool import (
    InfrastructureA2AConnectionPoolAdapter,
    InfrastructureMCPConnectionPoolAdapter,
)
from app.infrastructure.adapters.execution_ports import SqlAlchemyCommandEnvelopeWriter
from app.infrastructure.adapters.inference_ports import (
    InfrastructureInferenceProviderAdapter,
    InfrastructureModelMetricsAdapter,
    ResilientLLMFactoryAdapter,
)
from app.infrastructure.adapters.object_storage import create_object_storage_adapter
from app.infrastructure.adapters.outbound_notifier import HttpEmailOutboundNotifier
from app.infrastructure.adapters.query_ports import (
    SqlAlchemyAuditSummaryQuery,
    SqlAlchemyComplianceEvidenceQuery,
    SqlAlchemyEvidenceSessionQuery,
    SqlAlchemyQuotaUsageQuery,
    SqlAlchemyUsageQuery,
)
from app.infrastructure.adapters.redis_capabilities import (
    RedisConnectivityProbe,
    RedisHintPublisher,
    RedisNotificationPublisher,
    RedisNotificationStreamFactory,
    RedisRateLimitStore,
    RedisSandboxActivityStore,
    RedisSandboxQuotaStore,
    RedisSessionListStreamFactory,
)
from app.infrastructure.adapters.reporting_ports import (
    HmacEvidenceSigner,
    MarkdownPdfRenderer,
    PrometheusGovernanceMetricsAdapter,
)
from app.infrastructure.adapters.security_ports import (
    FernetSecretEnvelopeAdapter,
    FernetVersionedSecretCipherAdapter,
    JwtTokenCodecAdapter,
)
from app.infrastructure.execution.postgres_public_projection import PostgresPublicProjection
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
from app.infrastructure.external.actuator_client import MCPActuatorClient
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
from app.infrastructure.external.image_generation.provider import ProviderImageGenerator
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.knowledge.web_connector import HttpWebDocumentGateway
from app.infrastructure.external.llm.circuit_breaker import LLMCircuitBreaker
from app.infrastructure.external.sandbox.factory import SandboxFactory
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.session_list_notifier import DebouncedSessionListPublisher
from app.infrastructure.observability.otel_adapter import OtelObservabilityAdapter
from app.infrastructure.repositories.postgres_runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher

RuntimePolicyRepositoryFactory = Callable[[ResourceBundle], RuntimePolicyRepository]


@dataclass(frozen=True)
class SharedServices:
    """Shared adapters and services required by API and kernel composition."""

    uow_factory: UnitOfWorkFactory
    runtime_policy_repository: RuntimePolicyRepository
    runtime_policy_reader: RuntimePolicyReader
    token_codec: TokenCodecPort
    secret_cipher: VersionedSecretCipher
    password_hasher: PasswordHashPort
    service_api_key_hasher: ServiceKeyPort
    application_urls: ApplicationUrls
    rate_limit_store: RedisRateLimitStore
    command_ingress: CommandIngress
    run_admission_service: RunAdmissionService
    run_control_service: RunControlService
    run_projection: PostgresRunProjection
    sandbox_factory: SandboxFactory
    object_storage: ObjectStoragePort
    file_storage: FileStorage
    activity_objects: ActivityObjectStore
    session_streams: RedisSessionListStreamFactory
    notification_streams: RedisNotificationStreamFactory
    auth_service: AuthService
    audit_service: AuditService
    usage_stats_service: UsageStatsService
    quota_service: QuotaService
    mcp_integration_service: MCPServerService
    a2a_integration_service: A2AIntegrationService
    inference_model_service: InferenceModelService
    inference_endpoint_service: InferenceEndpointService
    inference_binding_service: InferenceBindingService
    embedding_service: EmbeddingService
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
    resource_guard: ResourceGuardService
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
    mcp_connection_pool: InfrastructureMCPConnectionPoolAdapter
    a2a_connection_pool: InfrastructureA2AConnectionPoolAdapter
    json_parser: RepairJSONParser
    search_engine: BingSearchEngine
    image_generator: ProviderImageGenerator
    resilient_llm_factory: ResilientLLMFactoryAdapter
    observability: OtelObservabilityAdapter


class _PostgresHealthChecker:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def check(self) -> HealthStatus:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
            return HealthStatus(service="postgres", status="error", details=str(exc))
        return HealthStatus(service="postgres", status="ok")


class _RedisHealthChecker:
    def __init__(self, probe: RedisConnectivityProbe) -> None:
        self._probe = probe

    async def check(self) -> HealthStatus:
        connectivity = await self._probe.check()
        return HealthStatus(
            service="redis",
            status="ok" if connectivity.available else "error",
            details=connectivity.error_key or "",
        )


def _default_runtime_policy_repository(resources: ResourceBundle) -> RuntimePolicyRepository:
    return PostgresRuntimePolicyRepository(
        session_factory=resources.postgres.session_factory,
        authorization=AuthorizationContext.system("runtime-policy-reader"),
    )


def _application_urls(resources: ResourceBundle) -> ApplicationUrls:
    return ApplicationUrls(
        frontend_base_url=resources.settings.frontend_base_url,
        oauth_redirect_base=resources.settings.oauth_redirect_base,
    )


def _outbound_policy(resources: ResourceBundle) -> OutboundNetworkPolicy:
    settings = resources.settings
    return OutboundNetworkPolicy(
        allowed_ports=parse_allowed_ports(settings.outbound_allowed_ports),
        allow_private_hosts=frozenset(
            value.strip()
            for value in settings.outbound_private_host_allowlist.split(",")
            if value.strip()
        ),
    )


def _audit_keyring(resources: ResourceBundle) -> AuditVerificationKeyring:
    settings = resources.settings
    keys: dict[str, tuple[str, ...]] = {
        settings.audit_signing_key_id: (settings.audit_signing_key,),
    }
    keys.update(
        (str(key_id), (str(secret),))
        for key_id, secret in settings.audit_previous_signing_keys.items()
    )
    return AuditVerificationKeyring(keys=keys)


def _runtime_values(resources: ResourceBundle) -> ComplianceRuntimeValues:
    settings = resources.settings
    default_key = type(settings).model_fields["audit_signing_key"].default
    return ComplianceRuntimeValues(
        sandbox_driver=settings.sandbox_driver,
        metrics_token_configured=bool(settings.metrics_token),
        audit_signing_key_id=settings.audit_signing_key_id,
        signing_key_is_default=settings.audit_signing_key == default_key,
    )


def fixture_replay_enabled(settings) -> bool:
    """Keep replay fixtures impossible in production composition."""

    return bool(settings.env.lower() != "production" and settings.patrol_fixture_replay_enabled)


def _object_storage(resources: ResourceBundle) -> ObjectStoragePort:
    return create_object_storage_adapter(
        provider=resources.settings.storage_provider,
        client=resources.object_storage_client,
    )


def _file_storage(
    resources: ResourceBundle,
    *,
    uow_factory: UnitOfWorkFactory,
) -> FileStorage:
    settings = resources.settings
    if settings.storage_provider.strip().lower() == "minio":
        return MinioFileStorage(
            bucket=settings.minio_bucket,
            minio=resources.object_storage_client,
            uow_factory=uow_factory,
        )
    return CosFileStorage(
        bucket=settings.cos_bucket,
        cos=resources.object_storage_client,
        uow_factory=uow_factory,
    )


def build_shared_services(
    resources: ResourceBundle,
    *,
    supervisor: TaskSupervisor,
    runtime_policy_repository_factory: RuntimePolicyRepositoryFactory = (
        _default_runtime_policy_repository
    ),
) -> SharedServices:
    """Build one explicit service graph without starting background work."""

    settings = resources.settings
    cipher = ApiKeyCipher(
        settings.api_key_secret,
        key_id=settings.api_key_secret_id,
        previous_secrets=settings.api_key_previous_secrets,
    )
    versioned_cipher = FernetVersionedSecretCipherAdapter(cipher=cipher)
    uow_factory = create_uow_factory(
        session_factory=resources.postgres.session_factory,
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=versioned_cipher,
            audit_signing_key=settings.audit_signing_key,
            audit_signing_key_id=settings.audit_signing_key_id,
            database_authorization_signing_secret=settings.session_secret,
        ),
    )
    runtime_policy_repository = runtime_policy_repository_factory(resources)
    runtime_policy_reader = RuntimePolicyReader(
        repository=runtime_policy_repository,
        refresh_interval_seconds=settings.policy_head_refresh_interval_seconds,
        max_staleness_seconds=settings.policy_max_staleness_seconds,
    )

    redis = resources.general_redis
    hint_publisher = RedisHintPublisher(redis)
    session_publisher = DebouncedSessionListPublisher(
        publisher=hint_publisher,
        supervisor=supervisor,
    )
    session_streams = RedisSessionListStreamFactory(redis)
    notification_publisher = RedisNotificationPublisher(redis)
    notification_streams = RedisNotificationStreamFactory(redis)
    rate_limit_store = RedisRateLimitStore(redis)
    sandbox_quota_store = RedisSandboxQuotaStore(redis)
    sandbox_activity_store = RedisSandboxActivityStore(redis)
    sandbox_factory = SandboxFactory.from_settings(
        settings=settings,
        operations=runtime_policy_reader,
        quota_store=sandbox_quota_store,
        activity_store=sandbox_activity_store,
    )

    password_hasher = PasswordHasher()
    jwt_service = JwtService(
        secret=settings.jwt_secret,
        access_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_token_ttl_seconds,
    )
    token_codec = JwtTokenCodecAdapter(codec=jwt_service)
    secret_envelope = FernetSecretEnvelopeAdapter(cipher=cipher)
    application_urls = _application_urls(resources)
    outbound_policy = _outbound_policy(resources)
    service_api_key_hasher = ServiceApiKeyHasher()
    inference_provider = InfrastructureInferenceProviderAdapter(
        outbound_policy=outbound_policy,
    )
    model_metrics = InfrastructureModelMetricsAdapter()
    llm_breaker = LLMCircuitBreaker(redis=redis)
    resilient_llm_factory = ResilientLLMFactoryAdapter(
        breaker=llm_breaker,
        provider_catalog=inference_provider,
        model_client_factory=inference_provider,
        metrics=model_metrics,
    )
    report_renderer = MarkdownPdfRenderer()
    evidence_signer = HmacEvidenceSigner(
        key_id=settings.audit_signing_key_id,
        secret=settings.audit_signing_key,
    )
    governance_metrics = PrometheusGovernanceMetricsAdapter()

    audit_summary_query = SqlAlchemyAuditSummaryQuery(
        session_factory=resources.postgres.session_factory
    )
    compliance_evidence_query = SqlAlchemyComplianceEvidenceQuery(
        session_factory=resources.postgres.session_factory,
        login_actions=LOGIN_AUDIT_ACTIONS,
        evidence_export_action=EVIDENCE_EXPORT_AUDIT_ACTION,
        admin_action_prefix=ADMIN_AUDIT_ACTION_PREFIX,
    )
    evidence_session_query = SqlAlchemyEvidenceSessionQuery(
        session_factory=resources.postgres.session_factory
    )
    quota_usage_query = SqlAlchemyQuotaUsageQuery(
        session_factory=resources.postgres.session_factory
    )
    usage_query = SqlAlchemyUsageQuery(session_factory=resources.postgres.session_factory)

    observability = OtelObservabilityAdapter(settings=settings)
    mcp_connection_pool = InfrastructureMCPConnectionPoolAdapter(
        outbound_policy=outbound_policy,
    )
    a2a_connection_pool = InfrastructureA2AConnectionPoolAdapter(
        outbound_policy=outbound_policy,
    )
    json_parser = RepairJSONParser()
    search_engine = BingSearchEngine()
    image_generator = ProviderImageGenerator(outbound_policy=outbound_policy)
    object_storage = _object_storage(resources)
    file_storage = _file_storage(resources, uow_factory=uow_factory)
    activity_objects = ActivityObjectStore(object_storage)
    command_ingress = CommandIngress(
        writer=SqlAlchemyCommandEnvelopeWriter(
            session_factory=resources.postgres.session_factory,
            authorization=None,
        )
    )
    run_admission_service = RunAdmissionService(
        command_ingress=command_ingress,
        activity_objects=activity_objects,
        policy_heads=runtime_policy_reader,
    )
    public_projection = PostgresPublicProjection(
        session_factory=resources.postgres.session_factory,
        authorization=None,
        cursor=PublicEventCursor(secret=hashlib.sha256(settings.api_key_secret.encode()).digest()),
    )
    run_projection = PostgresRunProjection(
        session_factory=resources.postgres.session_factory,
        authorization=None,
    )
    run_control_service = RunControlService(
        commands=command_ingress,
        run_projection=run_projection,
        public_projection=public_projection,
    )
    web_documents = HttpWebDocumentGateway(policy_reader=runtime_policy_reader)

    auth_service = AuthService(
        uow_factory=uow_factory,
        password_hasher=password_hasher,
        token_codec=token_codec,
    )
    audit_service = AuditService(
        uow_factory=uow_factory,
        verification_keyring=_audit_keyring(resources),
        governance_metrics=governance_metrics,
        summary_query=audit_summary_query,
    )
    mcp_integration_service = MCPServerService(
        uow_factory=uow_factory,
        secret_envelope=secret_envelope,
        outbound_policy=outbound_policy,
        audit_service=audit_service,
    )
    a2a_integration_service = A2AIntegrationService(
        uow_factory=uow_factory,
        outbound_policy=outbound_policy,
        audit_service=audit_service,
    )
    compliance_service = ComplianceService(
        evidence_query=compliance_evidence_query,
        audit_service=audit_service,
        run_projection=run_projection,
        runtime_values=_runtime_values(resources),
        policy_heads=runtime_policy_reader,
        report_renderer=report_renderer,
    )
    governance_profile_service = GovernanceProfileService(
        uow_factory=uow_factory,
        run_projection=run_projection,
    )
    governance_overview_service = GovernanceOverviewService(
        uow_factory=uow_factory,
        audit_service=audit_service,
        approval_projection=run_projection,
    )
    usage_stats_service = UsageStatsService(query=usage_query)
    quota_service = QuotaService(
        uow_factory=uow_factory,
        usage_query=quota_usage_query,
    )
    inference_model_service = InferenceModelService(
        uow_factory=uow_factory,
        provider_catalog=inference_provider,
        model_client_factory=inference_provider,
        embedding_factory=inference_provider,
    )
    inference_endpoint_service = InferenceEndpointService(
        uow_factory=uow_factory,
        cipher=versioned_cipher,
        outbound_policy=outbound_policy,
        provider_catalog=inference_provider,
    )
    inference_binding_service = InferenceBindingService(
        uow_factory=uow_factory,
        provider_catalog=inference_provider,
    )
    embedding_service = EmbeddingService(
        bindings=inference_binding_service,
        models=inference_model_service,
        embedding_factory=inference_provider,
    )
    capability_service = CapabilityService(
        bindings=inference_binding_service,
        policy_heads=runtime_policy_reader,
    )
    inference_status_service = InferenceStatusService(
        models=inference_model_service,
        capabilities=capability_service,
        policy_heads=runtime_policy_reader,
        breaker=llm_breaker,
        metrics=model_metrics,
    )
    skill_service = SkillService(uow_factory=uow_factory)
    team_service = TeamService(
        uow_factory=uow_factory,
        password_hasher=password_hasher,
        application_urls=application_urls,
    )
    service_api_key_service = ServiceApiKeyService(
        uow_factory=uow_factory,
        hasher=service_api_key_hasher,
    )
    memory_service = MemoryService(
        uow_factory=uow_factory,
        embeddings=embedding_service,
    )
    llm_token_usage_service = LLMTokenUsageService(uow_factory=uow_factory)
    file_service = FileService(uow_factory=uow_factory, file_storage=file_storage)

    knowledge_version_service = KnowledgeVersionService(uow_factory=uow_factory)
    version_providers = ResourceVersionProviderRegistry(providers=[knowledge_version_service])
    resource_binding_service = ResourceBindingService(
        uow_factory=uow_factory,
        providers=version_providers,
    )
    resource_guard = ResourceGuardService(providers=version_providers)
    session_service = SessionService(
        uow_factory=uow_factory,
        sandbox_factory=sandbox_factory,
        run_projection=run_projection,
        session_list_publisher=session_publisher,
        resource_guard=resource_guard,
        resource_binding_service=resource_binding_service,
    )
    knowledge_base_service = KnowledgeBaseService(
        uow_factory=uow_factory,
        file_storage=file_storage,
        resource_guard=resource_guard,
        resource_binding_service=resource_binding_service,
        run_admission_service=run_admission_service,
        run_control_service=run_control_service,
        run_projection=run_projection,
        web_documents=web_documents,
        inference_bindings=inference_binding_service,
    )
    artifact_service = ArtifactService(
        uow_factory=uow_factory,
        object_storage=object_storage,
        file_storage=file_storage,
    )
    evidence_service = EvidenceService(
        uow_factory=uow_factory,
        audit_service=audit_service,
        artifact_service=artifact_service,
        governance_profile_service=governance_profile_service,
        report_renderer=report_renderer,
        evidence_signer=evidence_signer,
        session_query=evidence_session_query,
    )
    outbound_notifier = HttpEmailOutboundNotifier(
        outbound_policy=outbound_policy,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_password=settings.smtp_password,
        smtp_from=settings.smtp_from,
        smtp_use_tls=settings.smtp_use_tls,
    )
    notification_service = NotificationService(
        uow_factory=uow_factory,
        mcp_servers=mcp_integration_service,
        mcp_connection_pool=mcp_connection_pool,
        policy_reader=runtime_policy_reader,
        publisher=notification_publisher,
        outbound_notifier=outbound_notifier,
    )
    actuator_client = MCPActuatorClient(connection_pool=mcp_connection_pool)
    patrol_pack_service = PatrolPackService(
        uow_factory=uow_factory,
        audit_service=audit_service,
        run_admission_service=run_admission_service,
    )
    patrol_run_service = PatrolRunService(
        uow_factory=uow_factory,
        audit_service=audit_service,
        artifact_service=artifact_service,
        notification_service=notification_service,
        run_admission_service=run_admission_service,
        command_ingress=command_ingress,
        policy_reader=runtime_policy_reader,
        fixture_replay_enabled=fixture_replay_enabled(settings),
        governance_metrics=governance_metrics,
    )
    patrol_evidence_service = PatrolEvidenceService(
        uow_factory=uow_factory,
        evidence_service=evidence_service,
        audit_service=audit_service,
        evidence_signer=evidence_signer,
    )
    patrol_remediation_service = PatrolRemediationService(
        uow_factory=uow_factory,
        audit_service=audit_service,
        actuator_client=actuator_client,
        patrol_run_service=patrol_run_service,
        run_admission_service=run_admission_service,
        policy_reader=runtime_policy_reader,
        governance_metrics=governance_metrics,
    )
    scheduled_job_service = ScheduledJobService(
        uow_factory=uow_factory,
        patrol_run_service=patrol_run_service,
        resource_guard=resource_guard,
        resource_binding_service=resource_binding_service,
        run_admission_service=run_admission_service,
        run_projection=run_projection,
        policy_reader=runtime_policy_reader,
        notification_service=notification_service,
        secret_cipher=versioned_cipher,
    )
    agent_service = AgentService(
        uow_factory=uow_factory,
        admission_service=run_admission_service,
        command_ingress=command_ingress,
        public_projection=public_projection,
        run_projection=run_projection,
    )
    a2a_server_service = A2AServerService(
        agent_service=agent_service,
        session_service=session_service,
        skill_service=skill_service,
        inference_model_service=inference_model_service,
        policy_heads=runtime_policy_reader,
        breaker=llm_breaker,
    )
    status_service = StatusService(
        checkers=[
            _PostgresHealthChecker(resources.postgres.session_factory),
            _RedisHealthChecker(RedisConnectivityProbe(redis)),
        ],
        policy_reader=runtime_policy_reader,
    )

    return SharedServices(
        uow_factory=uow_factory,
        runtime_policy_repository=runtime_policy_repository,
        runtime_policy_reader=runtime_policy_reader,
        token_codec=token_codec,
        secret_cipher=versioned_cipher,
        password_hasher=password_hasher,
        service_api_key_hasher=service_api_key_hasher,
        application_urls=application_urls,
        rate_limit_store=rate_limit_store,
        command_ingress=command_ingress,
        run_admission_service=run_admission_service,
        run_control_service=run_control_service,
        run_projection=run_projection,
        sandbox_factory=sandbox_factory,
        object_storage=object_storage,
        file_storage=file_storage,
        activity_objects=activity_objects,
        session_streams=session_streams,
        notification_streams=notification_streams,
        auth_service=auth_service,
        audit_service=audit_service,
        usage_stats_service=usage_stats_service,
        quota_service=quota_service,
        mcp_integration_service=mcp_integration_service,
        a2a_integration_service=a2a_integration_service,
        inference_model_service=inference_model_service,
        inference_endpoint_service=inference_endpoint_service,
        inference_binding_service=inference_binding_service,
        embedding_service=embedding_service,
        capability_service=capability_service,
        inference_status_service=inference_status_service,
        skill_service=skill_service,
        team_service=team_service,
        service_api_key_service=service_api_key_service,
        memory_service=memory_service,
        llm_token_usage_service=llm_token_usage_service,
        status_service=status_service,
        file_service=file_service,
        session_service=session_service,
        resource_binding_service=resource_binding_service,
        resource_guard=resource_guard,
        agent_service=agent_service,
        knowledge_base_service=knowledge_base_service,
        a2a_server_service=a2a_server_service,
        artifact_service=artifact_service,
        notification_service=notification_service,
        scheduled_job_service=scheduled_job_service,
        evidence_service=evidence_service,
        patrol_pack_service=patrol_pack_service,
        patrol_run_service=patrol_run_service,
        patrol_evidence_service=patrol_evidence_service,
        patrol_remediation_service=patrol_remediation_service,
        compliance_service=compliance_service,
        governance_profile_service=governance_profile_service,
        governance_overview_service=governance_overview_service,
        mcp_connection_pool=mcp_connection_pool,
        a2a_connection_pool=a2a_connection_pool,
        json_parser=json_parser,
        search_engine=search_engine,
        image_generator=image_generator,
        resilient_llm_factory=resilient_llm_factory,
        observability=observability,
    )
