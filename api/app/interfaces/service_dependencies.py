#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.a2a_server_service import A2AServerService
from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.codebase_service import CodebaseService
from app.application.services.file_service import FileService
from app.application.services.llm_status_service import LLMStatusService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.marketplace_service import MarketplaceService
from app.application.services.memory_service import MemoryService
from app.application.services.questionnaire_service import QuestionnaireService
from app.application.services.room_service import RoomService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.container import ApiContainer
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.storage.redis import RedisClient

logger = logging.getLogger(__name__)


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
async def get_skill_service(
        service: SkillService = Depends(Provide[ApiContainer.skill_service]),
) -> SkillService:
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
async def get_questionnaire_service(
        service: QuestionnaireService = Depends(Provide[ApiContainer.questionnaire_service]),
) -> QuestionnaireService:
    return service


@inject
async def get_room_service(
        service: RoomService = Depends(Provide[ApiContainer.room_service]),
) -> RoomService:
    return service


@inject
async def get_marketplace_service(
        service: MarketplaceService = Depends(Provide[ApiContainer.marketplace_service]),
) -> MarketplaceService:
    return service


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
async def get_a2a_server_service(
        service: A2AServerService = Depends(Provide[ApiContainer.a2a_server_service]),
) -> A2AServerService:
    return service
