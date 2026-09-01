"""Typed FastAPI accessors for the lifespan-owned API runtime."""

from __future__ import annotations

from fastapi import Depends, Request, WebSocket

from app.application.ports.coordination import RateLimitStorePort
from app.application.ports.crypto import (
    ApplicationUrls,
    CookieManagerPort,
    CsrfPort,
    OAuthRegistryPort,
    ServiceKeyPort,
    TokenCodecPort,
)
from app.application.ports.streams import (
    NotificationStreamFactory,
    SessionListStreamFactory,
)
from app.application.services.a2a_server_service import A2AServerService
from app.application.services.agent_service import AgentService
from app.application.services.approval_inbox_service import ApprovalInboxService
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
from app.application.services.patrol_run_service import PatrolRunService
from app.application.services.quota_service import QuotaService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.runtime_policy_reader import RuntimePolicyReader
from app.application.services.runtime_policy_service import RuntimePolicyService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.application.services.service_api_key_service import ServiceApiKeyService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageStatsService
from app.composition.types import ApiRuntime
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.sandbox import SandboxFactoryPort
from app.domain.repositories.uow import UnitOfWorkFactory


class ApiRuntimeUnavailableError(RuntimeError):
    """The ASGI lifespan has not installed its process runtime."""


def require_api_runtime(request: Request) -> ApiRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailableError("API runtime is not initialized")
    return runtime


def require_websocket_api_runtime(websocket: WebSocket) -> ApiRuntime:
    runtime = getattr(websocket.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailableError("API runtime is not initialized")
    return runtime


def get_uow_factory(runtime: ApiRuntime = Depends(require_api_runtime)) -> UnitOfWorkFactory:
    return runtime.uow_factory


def get_token_codec(runtime: ApiRuntime = Depends(require_api_runtime)) -> TokenCodecPort:
    return runtime.token_codec


def get_service_key_hasher(runtime: ApiRuntime = Depends(require_api_runtime)) -> ServiceKeyPort:
    return runtime.service_api_key_hasher


def get_csrf_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> CsrfPort:
    return runtime.csrf_service


def get_oauth_registry(runtime: ApiRuntime = Depends(require_api_runtime)) -> OAuthRegistryPort:
    return runtime.oauth_registry


def get_application_urls(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ApplicationUrls:
    return runtime.application_urls


def get_rate_limit_store(runtime: ApiRuntime = Depends(require_api_runtime)) -> RateLimitStorePort:
    return runtime.rate_limit_store


def get_sandbox_factory(runtime: ApiRuntime = Depends(require_api_runtime)) -> SandboxFactoryPort:
    return runtime.sandbox_factory


def get_auth_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> AuthService:
    return runtime.auth_service


def get_audit_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> AuditService:
    return runtime.audit_service


def get_usage_stats_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> UsageStatsService:
    return runtime.usage_stats_service


def get_quota_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> QuotaService:
    return runtime.quota_service


def get_cookie_manager(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> CookieManagerPort:
    return runtime.cookie_manager


def get_mcp_integration_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> MCPServerService:
    return runtime.mcp_integration_service


def get_a2a_integration_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> A2AIntegrationService:
    return runtime.a2a_integration_service


def get_inference_model_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> InferenceModelService:
    return runtime.inference_model_service


def get_inference_endpoint_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> InferenceEndpointService:
    return runtime.inference_endpoint_service


def get_inference_binding_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> InferenceBindingService:
    return runtime.inference_binding_service


def get_capability_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> CapabilityService:
    return runtime.capability_service


def get_inference_status_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> InferenceStatusService:
    return runtime.inference_status_service


def get_skill_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> SkillService:
    return runtime.skill_service


def get_team_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> TeamService:
    return runtime.team_service


def get_service_api_key_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ServiceApiKeyService:
    return runtime.service_api_key_service


def get_memory_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> MemoryService:
    return runtime.memory_service


def get_runtime_policy_reader(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> RuntimePolicyReader:
    return runtime.runtime_policy_reader


def get_runtime_policy_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> RuntimePolicyService:
    return runtime.runtime_policy_service


def get_llm_token_usage_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> LLMTokenUsageService:
    return runtime.llm_token_usage_service


def get_status_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> StatusService:
    return runtime.status_service


def get_object_storage(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ObjectStoragePort:
    return runtime.object_storage


def get_file_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> FileService:
    return runtime.file_service


def get_session_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> SessionService:
    return runtime.session_service


def get_session_list_stream_factory(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> SessionListStreamFactory:
    return runtime.session_streams


def get_notification_stream_factory(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> NotificationStreamFactory:
    return runtime.notification_streams


def get_resource_binding_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ResourceBindingService:
    return runtime.resource_binding_service


def get_agent_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> AgentService:
    return runtime.agent_service


def get_knowledge_base_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> KnowledgeBaseService:
    return runtime.knowledge_base_service


def get_a2a_server_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> A2AServerService:
    return runtime.a2a_server_service


def get_artifact_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> ArtifactService:
    return runtime.artifact_service


def get_notification_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> NotificationService:
    return runtime.notification_service


def get_scheduled_job_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ScheduledJobService:
    return runtime.scheduled_job_service


def get_evidence_service(runtime: ApiRuntime = Depends(require_api_runtime)) -> EvidenceService:
    return runtime.evidence_service


def get_patrol_pack_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> PatrolPackService:
    return runtime.patrol_pack_service


def get_patrol_run_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> PatrolRunService:
    return runtime.patrol_run_service


def get_patrol_evidence_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> PatrolEvidenceService:
    return runtime.patrol_evidence_service


def get_patrol_remediation_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> PatrolRemediationService:
    return runtime.patrol_remediation_service


def get_compliance_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ComplianceService:
    return runtime.compliance_service


def get_governance_profile_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> GovernanceProfileService:
    return runtime.governance_profile_service


def get_governance_overview_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> GovernanceOverviewService:
    return runtime.governance_overview_service


def get_approval_inbox_service(
    runtime: ApiRuntime = Depends(require_api_runtime),
) -> ApprovalInboxService:
    # A stateless read-model wrapper over the shared run projection; cheap to
    # build per request, so it needs no slot in the runtime bundle.
    return ApprovalInboxService(run_projection=runtime.run_projection)
