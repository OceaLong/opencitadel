#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.a2a_server_service import A2AServerService
from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.auth_service import AuthService
from app.application.services.audit_service import AuditService
from app.application.services.compliance_service import ComplianceService
from app.application.services.codebase_service import CodebaseService
from app.application.services.evidence_service import EvidenceService
from app.application.services.governance_overview_service import GovernanceOverviewService
from app.application.services.governance_profile_service import GovernanceProfileService
from app.application.services.file_service import FileService
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.llm_status_service import LLMStatusService
from app.application.services.llm_endpoint_service import LLMEndpointService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.memory_service import MemoryService
from app.application.services.quota_service import QuotaService
from app.application.services.patrol_evidence_service import PatrolEvidenceService
from app.application.services.patrol_pack_service import PatrolPackService
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.application.services.patrol_run_service import PatrolRunService
from app.application.services.session_service import SessionService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_build_service import ResourceBuildService
from app.application.services.service_api_key_service import ServiceApiKeyService
from app.application.services.skill_service import SkillService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageStatsService
from app.application.services.status_service import StatusService
from app.container import ApiContainer
from app.domain.external.object_storage import ObjectStoragePort
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.storage.redis import RedisClient
from app.infrastructure.external.resource_build_event_notifier import (
    RedisResourceBuildEventNotifier,
)
from app.infrastructure.security.cookie import AuthCookieManager

logger = logging.getLogger(__name__)


@inject
async def get_auth_service(
        service: AuthService = Depends(Provide[ApiContainer.auth_service]),
) -> AuthService:
    return service


@inject
async def get_audit_service(
        service: AuditService = Depends(Provide[ApiContainer.audit_service]),
) -> AuditService:
    return service


@inject
async def get_usage_stats_service(
        service: UsageStatsService = Depends(Provide[ApiContainer.usage_stats_service]),
) -> UsageStatsService:
    return service


@inject
async def get_quota_service(
        service: QuotaService = Depends(Provide[ApiContainer.quota_service]),
) -> QuotaService:
    return service


@inject
async def get_cookie_manager(
        manager: AuthCookieManager = Depends(Provide[ApiContainer.cookie_manager]),
) -> AuthCookieManager:
    return manager


@inject
async def get_app_config_service(
        service: AppConfigService = Depends(Provide[ApiContainer.app_config_service]),
) -> AppConfigService:
    return service


@inject
async def get_llm_model_service(
        service: LLMModelService = Depends(Provide[ApiContainer.llm_model_service]),
) -> LLMModelService:
    return service


@inject
async def get_llm_endpoint_service(
        service: LLMEndpointService = Depends(Provide[ApiContainer.llm_endpoint_service]),
) -> LLMEndpointService:
    return service


@inject
async def get_skill_service(
        service: SkillService = Depends(Provide[ApiContainer.skill_service]),
) -> SkillService:
    return service


@inject
async def get_team_service(
        service: TeamService = Depends(Provide[ApiContainer.team_service]),
) -> TeamService:
    return service


@inject
async def get_service_api_key_service(
        service: ServiceApiKeyService = Depends(Provide[ApiContainer.service_api_key_service]),
) -> ServiceApiKeyService:
    return service


@inject
async def get_memory_service(
        service: MemoryService = Depends(Provide[ApiContainer.memory_service]),
) -> MemoryService:
    return service


@inject
async def get_llm_token_usage_service(
        service: LLMTokenUsageService = Depends(Provide[ApiContainer.llm_token_usage_service]),
) -> LLMTokenUsageService:
    return service


@inject
async def get_status_service(
        db_session: AsyncSession = Depends(get_db_session),
        redis_client: RedisClient = Depends(Provide[ApiContainer.redis]),
) -> StatusService:
    postgres_checker = PostgresHealthChecker(db_session)
    redis_checker = RedisHealthChecker(redis_client)
    return StatusService(checkers=[postgres_checker, redis_checker])


@inject
async def get_llm_status_service(
        llm_model_service: LLMModelService = Depends(Provide[ApiContainer.llm_model_service]),
) -> LLMStatusService:
    return LLMStatusService(llm_model_service=llm_model_service)


@inject
async def get_object_storage(
        storage: ObjectStoragePort = Depends(Provide[ApiContainer.object_storage]),
) -> ObjectStoragePort:
    return storage


@inject
async def get_file_service(
        service: FileService = Depends(Provide[ApiContainer.file_service]),
) -> FileService:
    return service


@inject
async def get_session_service(
        service: SessionService = Depends(Provide[ApiContainer.session_service]),
) -> SessionService:
    return service


@inject
async def get_resource_binding_service(
        service: ResourceBindingService = Depends(
            Provide[ApiContainer.resource_binding_service]
        ),
) -> ResourceBindingService:
    return service


@inject
async def get_resource_build_service(
        service: ResourceBuildService = Depends(
            Provide[ApiContainer.resource_build_service]
        ),
) -> ResourceBuildService:
    return service


@inject
async def get_resource_build_event_notifier(
        notifier: RedisResourceBuildEventNotifier = Depends(
            Provide[ApiContainer.resource_build_event_notifier]
        ),
) -> RedisResourceBuildEventNotifier:
    return notifier


@inject
async def get_agent_service(
        service: AgentService = Depends(Provide[ApiContainer.agent_service]),
) -> AgentService:
    return service


@inject
async def get_codebase_service(
        service: CodebaseService = Depends(Provide[ApiContainer.codebase_service]),
) -> CodebaseService:
    return service


@inject
async def get_knowledge_base_service(
        service: KnowledgeBaseService = Depends(Provide[ApiContainer.knowledge_base_service]),
) -> KnowledgeBaseService:
    return service


@inject
async def get_a2a_server_service(
        service: A2AServerService = Depends(Provide[ApiContainer.a2a_server_service]),
) -> A2AServerService:
    return service


@inject
async def get_artifact_service(
        service=Depends(Provide[ApiContainer.artifact_service]),
):
    return service


@inject
async def get_notification_service(
        service=Depends(Provide[ApiContainer.notification_service]),
):
    return service


@inject
async def get_scheduled_job_service(
        service=Depends(Provide[ApiContainer.scheduled_job_service]),
):
    return service


@inject
async def get_evidence_service(
        service: EvidenceService = Depends(Provide[ApiContainer.evidence_service]),
) -> EvidenceService:
    return service


@inject
async def get_patrol_pack_service(
        service: PatrolPackService = Depends(Provide[ApiContainer.patrol_pack_service]),
) -> PatrolPackService:
    return service


@inject
async def get_patrol_run_service(
        service: PatrolRunService = Depends(Provide[ApiContainer.patrol_run_service]),
) -> PatrolRunService:
    return service


@inject
async def get_patrol_evidence_service(
        service: PatrolEvidenceService = Depends(Provide[ApiContainer.patrol_evidence_service]),
) -> PatrolEvidenceService:
    return service


@inject
async def get_patrol_remediation_service(
        service: PatrolRemediationService = Depends(Provide[ApiContainer.patrol_remediation_service]),
) -> PatrolRemediationService:
    return service


@inject
async def get_compliance_service(
        service: ComplianceService = Depends(Provide[ApiContainer.compliance_service]),
) -> ComplianceService:
    return service


@inject
async def get_governance_profile_service(
        service: GovernanceProfileService = Depends(
            Provide[ApiContainer.governance_profile_service]
        ),
) -> GovernanceProfileService:
    return service


@inject
async def get_governance_overview_service(
        service: GovernanceOverviewService = Depends(
            Provide[ApiContainer.governance_overview_service]
        ),
) -> GovernanceOverviewService:
    return service
