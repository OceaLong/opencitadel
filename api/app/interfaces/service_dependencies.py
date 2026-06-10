#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.a2a_server_service import A2AServerService
from app.application.services.codebase_service import CodebaseService
from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.file_service import FileService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.marketplace_service import MarketplaceService
from app.application.services.questionnaire_service import QuestionnaireService
from app.application.services.room_service import RoomService
from app.application.services.memory_service import MemoryService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.domain.services.checkpoint_service import CheckpointService
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.application.services.config_provider import get_app_config_provider, get_runtime_config
from app.application.services.app_config_repository_factory import create_app_config_repository
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.storage.cos import Cos, get_cos
from app.infrastructure.storage.postgres import get_db_session, get_uow
from app.infrastructure.storage.redis import RedisClient, get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_cipher() -> ApiKeyCipher:
    return ApiKeyCipher(settings.api_key_secret)


@lru_cache()
def get_app_config_service() -> AppConfigService:
    logger.info("加载获取AppConfigService")
    return AppConfigService(app_config_repository=create_app_config_repository())


@lru_cache()
def get_llm_model_service() -> LLMModelService:
    return LLMModelService(uow_factory=get_uow, cipher=get_cipher())


@lru_cache()
def get_skill_service() -> SkillService:
    return SkillService(uow_factory=get_uow)


def get_memory_service() -> MemoryService:
    return MemoryService(uow_factory=get_uow)


@lru_cache()
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


@lru_cache()
def get_file_service() -> FileService:
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=get_cos(),
        uow_factory=get_uow,
    )
    return FileService(uow_factory=get_uow, file_storage=file_storage)


@lru_cache()
def get_session_service() -> SessionService:
    return SessionService(uow_factory=get_uow, sandbox_cls=DockerSandbox)


@lru_cache()
def get_questionnaire_service() -> QuestionnaireService:
    return QuestionnaireService(uow_factory=get_uow)


def get_room_service(
        redis_client: RedisClient = Depends(get_redis),
) -> RoomService:
    return RoomService(uow_factory=get_uow, redis_client=redis_client)


def get_marketplace_service(
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        file_service: FileService = Depends(get_file_service),
) -> MarketplaceService:
    return MarketplaceService(
        llm_model_service=llm_model_service,
        file_service=file_service,
        uow_factory=get_uow,
    )


@lru_cache()
def get_checkpoint_service() -> CheckpointService:
    return CheckpointService(
        uow_factory=get_uow,
        cos=get_cos(),
        sandbox_cls=DockerSandbox,
    )


def get_agent_service() -> AgentService:
    config_provider = get_app_config_provider()
    runtime_config = get_runtime_config()
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=get_cos(),
        uow_factory=get_uow,
    )
    return AgentService(
        uow_factory=get_uow,
        llm_model_service=get_llm_model_service(),
        skill_service=get_skill_service(),
        memory_service=get_memory_service(),
        agent_config=runtime_config.agent_config,
        mcp_config=runtime_config.mcp_config,
        a2a_config=runtime_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        auto_extract_memory=runtime_config.memory.auto_extract_enabled,
        config_provider=config_provider,
        checkpoint_service=get_checkpoint_service(),
    )


@lru_cache()
def get_codebase_service() -> CodebaseService:
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=get_cos(),
        uow_factory=get_uow,
    )
    return CodebaseService(
        uow_factory=get_uow,
        sandbox_cls=DockerSandbox,
        file_storage=file_storage,
    )


@lru_cache()
def get_a2a_server_service() -> A2AServerService:
    return A2AServerService(
        agent_service=get_agent_service(),
        session_service=get_session_service(),
        skill_service=get_skill_service(),
    )
