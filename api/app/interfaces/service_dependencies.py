#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.file_service import FileService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.marketplace_service import MarketplaceService
from app.application.services.memory_service import MemoryService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.application.services.config_provider import get_app_config_provider
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.storage.cos import Cos, get_cos
from app.infrastructure.storage.postgres import get_db_session, get_uow
from app.infrastructure.storage.redis import RedisClient, get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_cipher() -> ApiKeyCipher:
    return ApiKeyCipher(settings.api_key_secret)


def get_app_config_service() -> AppConfigService:
    logger.info("加载获取AppConfigService")
    file_app_config_repository = FileAppConfigRepository(settings.app_config_filepath)
    return AppConfigService(app_config_repository=file_app_config_repository)


def get_llm_model_service(cipher: ApiKeyCipher = Depends(get_cipher)) -> LLMModelService:
    return LLMModelService(uow_factory=get_uow, cipher=cipher)


def get_skill_service() -> SkillService:
    return SkillService(uow_factory=get_uow)


def get_memory_service() -> MemoryService:
    return MemoryService(uow_factory=get_uow, recall_limit=settings.memory_recall_limit)


def get_llm_token_usage_service() -> LLMTokenUsageService:
    return LLMTokenUsageService(uow_factory=get_uow)


def get_status_service(
        db_session: AsyncSession = Depends(get_db_session),
        redis_client: RedisClient = Depends(get_redis),
) -> StatusService:
    postgres_checker = PostgresHealthChecker(db_session)
    redis_checker = RedisHealthChecker(redis_client)
    logger.info("加载获取StatusService")
    return StatusService(checkers=[postgres_checker, redis_checker])


def get_file_service(cos: Cos = Depends(get_cos)) -> FileService:
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=cos,
        uow_factory=get_uow,
    )
    return FileService(uow_factory=get_uow, file_storage=file_storage)


def get_session_service() -> SessionService:
    return SessionService(uow_factory=get_uow, sandbox_cls=DockerSandbox)


def get_marketplace_service(
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        file_service: FileService = Depends(get_file_service),
) -> MarketplaceService:
    return MarketplaceService(
        llm_model_service=llm_model_service,
        file_service=file_service,
    )


def get_agent_service(
        cos: Cos = Depends(get_cos),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        skill_service: SkillService = Depends(get_skill_service),
        memory_service: MemoryService = Depends(get_memory_service),
) -> AgentService:
    config_provider = get_app_config_provider()
    app_config = config_provider._cache or config_provider._repository.load()
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=cos,
        uow_factory=get_uow,
    )
    return AgentService(
        uow_factory=get_uow,
        llm_model_service=llm_model_service,
        skill_service=skill_service,
        memory_service=memory_service,
        agent_config=app_config.agent_config,
        mcp_config=app_config.mcp_config,
        a2a_config=app_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        auto_extract_memory=settings.memory_auto_extract_enabled,
        config_provider=config_provider,
    )
