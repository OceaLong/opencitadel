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
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.marketplace_service import MarketplaceService
from app.application.services.memory_service import MemoryService
from app.application.services.questionnaire_service import QuestionnaireService
from app.application.services.room_service import RoomService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.container import AppContainer
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.storage.redis import RedisClient

logger = logging.getLogger(__name__)


@inject
def get_app_config_service(
        service: AppConfigService = Depends(Provide[AppContainer.app_config_service]),
) -> AppConfigService:
    return service


@inject
def get_llm_model_service(
        service: LLMModelService = Depends(Provide[AppContainer.llm_model_service]),
) -> LLMModelService:
    return service


@inject
def get_skill_service(
        service: SkillService = Depends(Provide[AppContainer.skill_service]),
) -> SkillService:
    return service


@inject
def get_memory_service(
        service: MemoryService = Depends(Provide[AppContainer.memory_service]),
) -> MemoryService:
    return service


@inject
def get_llm_token_usage_service(
        service: LLMTokenUsageService = Depends(Provide[AppContainer.llm_token_usage_service]),
) -> LLMTokenUsageService:
    return service


@inject
def get_status_service(
        db_session: AsyncSession = Depends(get_db_session),
        redis_client: RedisClient = Depends(Provide[AppContainer.redis]),
) -> StatusService:
    postgres_checker = PostgresHealthChecker(db_session)
    redis_checker = RedisHealthChecker(redis_client)
    return StatusService(checkers=[postgres_checker, redis_checker])


@inject
def get_file_service(
        service: FileService = Depends(Provide[AppContainer.file_service]),
) -> FileService:
    return service


@inject
def get_session_service(
        service: SessionService = Depends(Provide[AppContainer.session_service]),
) -> SessionService:
    return service


@inject
def get_questionnaire_service(
        service: QuestionnaireService = Depends(Provide[AppContainer.questionnaire_service]),
) -> QuestionnaireService:
    return service


@inject
def get_room_service(
        service: RoomService = Depends(Provide[AppContainer.room_service]),
) -> RoomService:
    return service


@inject
def get_marketplace_service(
        service: MarketplaceService = Depends(Provide[AppContainer.marketplace_service]),
) -> MarketplaceService:
    return service


@inject
def get_agent_service(
        service: AgentService = Depends(Provide[AppContainer.agent_service]),
) -> AgentService:
    return service


@inject
def get_codebase_service(
        service: CodebaseService = Depends(Provide[AppContainer.codebase_service]),
) -> CodebaseService:
    return service


@inject
def get_a2a_server_service(
        service: A2AServerService = Depends(Provide[AppContainer.a2a_server_service]),
) -> A2AServerService:
    return service


@inject
def get_task_runner_factory(
        factory: TaskRunnerFactory = Depends(Provide[AppContainer.task_runner_factory]),
) -> TaskRunnerFactory:
    return factory
