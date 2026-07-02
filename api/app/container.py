#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Role-scoped dependency-injection composition roots for API and Worker."""
from __future__ import annotations

import logging
from typing import Callable

from contextlib import asynccontextmanager

from dependency_injector import containers, providers

from app.application.services.a2a_server_service import A2AServerService
from app.application.services.agent_service import AgentService
from app.application.services.app_config_repository_factory import create_app_config_repository
from app.application.services.app_config_service import AppConfigService
from app.application.services.auth_service import AuthService
from app.application.services.audit_service import AuditService
from app.application.services.codebase_service import CodebaseService
from app.application.services.config_provider import AppConfigProvider, create_app_config_provider
from app.application.services.file_service import FileService
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.marketplace_service import MarketplaceService
from app.application.services.memory_service import MemoryService
from app.application.services.questionnaire_service import QuestionnaireService
from app.application.services.quota_service import QuotaService
from app.application.services.room_service import RoomService
from app.application.services.session_service import SessionService
from app.application.services.session_state_service import SessionStateService
from app.application.services.service_api_key_service import ServiceApiKeyService
from app.application.services.skill_service import SkillService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageStatsService
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.checkpoint_service import CheckpointService
from app.infrastructure.adapters.connection_pool import (
    InfrastructureA2AConnectionPoolAdapter,
    InfrastructureMCPConnectionPoolAdapter,
)
from app.infrastructure.adapters.domain_ports import (
    OtelObservabilityAdapter,
    RedisEventSequenceAdapter,
    RedisSessionListNotifierAdapter,
    RedisTaskStateAdapter,
)
from app.infrastructure.adapters.object_storage import CosObjectStorageAdapter, MinioObjectStorageAdapter
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.file_storage.minio_file_storage import MinioFileStorage
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.sandbox.sandbox_driver import get_sandbox_class
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.cookie import AuthCookieManager
from app.infrastructure.security.csrf import CsrfService
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.oauth_clients import OAuthClients
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher
from app.infrastructure.storage.factory import create_storage_client, set_active_storage_client
from app.infrastructure.storage.postgres import Postgres, get_postgres
from app.infrastructure.storage.redis import RedisClient, get_redis
from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_WIRE_PACKAGES = [
    "app.interfaces",
    "app.interfaces.endpoints",
]


def _uow_factory(postgres: Postgres) -> Callable[[], IUnitOfWork]:
    def factory() -> IUnitOfWork:
        return DBUnitOfWork(session_factory=postgres.session_factory)

    return factory


def _session_state_factory(
        uow_factory: Callable[[], IUnitOfWork],
        session_list_notifier: RedisSessionListNotifierAdapter,
) -> Callable[[], SessionStateService]:
    def factory() -> SessionStateService:
        return SessionStateService(
            uow_factory=uow_factory,
            session_list_notifier=session_list_notifier,
        )

    return factory


async def _init_postgres(postgres: Postgres) -> Postgres:
    await postgres.init()
    return postgres


async def _shutdown_postgres(postgres: Postgres) -> None:
    await postgres.shutdown()


async def _init_redis(redis: RedisClient) -> RedisClient:
    await redis.init()
    return redis


async def _shutdown_redis(redis: RedisClient) -> None:
    await redis.shutdown()


@asynccontextmanager
async def _storage_client_resource(settings: Settings):
    client = await create_storage_client(settings)
    set_active_storage_client(client)
    try:
        yield client
    finally:
        await client.shutdown()
        set_active_storage_client(None)


async def _sync_event_seq(_storage_client) -> None:
    from app.infrastructure.external.event_seq_allocator import sync_global_event_seq

    await sync_global_event_seq()


async def _warm_app_config(_postgres: Postgres, config_provider: AppConfigProvider) -> AppConfigProvider:
    await config_provider.get()
    return config_provider


async def _configure_runtime_from_config(config_provider: AppConfigProvider) -> None:
    from app.infrastructure.external.runtime_settings import (
        AdmissionRuntimeSettings,
        SandboxRuntimeSettings,
        TaskQueueRuntimeSettings,
        configure_admission_runtime,
    )
    from app.infrastructure.external.sandbox.docker_sandbox import configure_sandbox_runtime
    from app.infrastructure.external.task.task_state import configure_task_state_runtime

    app_config = await config_provider.get()
    configure_sandbox_runtime(SandboxRuntimeSettings.from_config(app_config.sandbox))
    configure_admission_runtime(
        AdmissionRuntimeSettings.from_config(app_config.sandbox, app_config.worker),
    )
    configure_task_state_runtime(
        TaskQueueRuntimeSettings.from_config(app_config.streams, app_config.worker),
    )


async def _start_sandbox_pool(_: None) -> None:
    from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

    await get_sandbox_pool().start()


async def _stop_sandbox_pool(_: None) -> None:
    from app.infrastructure.external.sandbox.sandbox_pool import get_sandbox_pool

    await get_sandbox_pool().shutdown()


async def _start_config_listener(_: None) -> None:
    from app.infrastructure.external.app_config_notifier import start_config_invalidate_listener

    await start_config_invalidate_listener()


async def _stop_config_listener(_: None) -> None:
    from app.infrastructure.external.app_config_notifier import stop_config_invalidate_listener

    await stop_config_invalidate_listener()


def _create_file_storage(storage_client, settings: Settings, uow_factory):
    provider = (settings.storage_provider or "cos").strip().lower()
    if provider == "minio":
        return MinioFileStorage(
            bucket=settings.minio_bucket,
            minio=storage_client,
            uow_factory=uow_factory,
        )
    return CosFileStorage(
        bucket=settings.cos_bucket,
        cos=storage_client,
        uow_factory=uow_factory,
    )


def _create_object_storage(storage_client, settings: Settings):
    provider = (settings.storage_provider or "cos").strip().lower()
    if provider == "minio":
        return MinioObjectStorageAdapter(minio=storage_client)
    return CosObjectStorageAdapter(cos=storage_client)


class BaseContainer(containers.DeclarativeContainer):
    """Shared infrastructure and application services for API and Worker."""

    config: providers.Singleton[Settings] = providers.Singleton(get_settings)

    postgres_client = providers.Singleton(get_postgres)
    redis_client = providers.Singleton(get_redis)

    postgres = providers.Resource(_init_postgres, postgres=postgres_client)
    redis = providers.Resource(_init_redis, redis=redis_client)
    storage_client = providers.Resource(_storage_client_resource, settings=config)
    event_seq_sync = providers.Resource(_sync_event_seq, _storage_client=storage_client)

    app_config_provider = providers.Singleton(create_app_config_provider)
    app_config_warmup = providers.Resource(
        _warm_app_config,
        _postgres=postgres,
        config_provider=app_config_provider,
    )
    runtime_configure = providers.Resource(
        _configure_runtime_from_config,
        config_provider=app_config_warmup,
    )

    sandbox_cls = providers.Callable(get_sandbox_class)
    task_cls = providers.Object(RedisStreamTask)

    cipher = providers.Factory(ApiKeyCipher, secret=config.provided.api_key_secret)
    password_hasher = providers.Singleton(PasswordHasher)
    jwt_service = providers.Singleton(
        JwtService,
        secret=config.provided.jwt_secret,
        access_ttl_seconds=config.provided.access_token_ttl_seconds,
        refresh_ttl_seconds=config.provided.refresh_token_ttl_seconds,
    )
    cookie_manager = providers.Singleton(
        AuthCookieManager,
        domain=config.provided.cookie_domain,
        secure=config.provided.cookie_secure,
        access_max_age=config.provided.access_token_ttl_seconds,
        refresh_max_age=config.provided.refresh_token_ttl_seconds,
    )
    csrf_service = providers.Singleton(CsrfService)
    oauth_clients = providers.Singleton(
        OAuthClients,
        google_client_id=config.provided.google_client_id,
        google_client_secret=config.provided.google_client_secret,
        github_client_id=config.provided.github_client_id,
        github_client_secret=config.provided.github_client_secret,
    )
    service_api_key_hasher = providers.Singleton(ServiceApiKeyHasher)
    uow_factory = providers.Callable(_uow_factory, postgres=postgres)

    task_state_port = providers.Singleton(RedisTaskStateAdapter)
    event_sequence_port = providers.Singleton(RedisEventSequenceAdapter)
    observability_port = providers.Singleton(OtelObservabilityAdapter)
    session_list_notifier = providers.Singleton(RedisSessionListNotifierAdapter)
    mcp_connection_pool = providers.Singleton(InfrastructureMCPConnectionPoolAdapter)
    a2a_connection_pool = providers.Singleton(InfrastructureA2AConnectionPoolAdapter)
    json_parser = providers.Singleton(RepairJSONParser)
    search_engine = providers.Singleton(BingSearchEngine)

    object_storage = providers.Singleton(
        _create_object_storage,
        storage_client=storage_client,
        settings=config,
    )
    file_storage = providers.Singleton(
        _create_file_storage,
        storage_client=storage_client,
        settings=config,
        uow_factory=uow_factory,
    )

    session_state_factory = providers.Callable(
        _session_state_factory,
        uow_factory=uow_factory,
        session_list_notifier=session_list_notifier,
    )

    checkpoint_service = providers.Singleton(
        CheckpointService,
        uow_factory=uow_factory,
        object_storage=object_storage,
        sandbox_cls=sandbox_cls,
        task_state_port=task_state_port,
    )

    app_config_service = providers.Singleton(
        AppConfigService,
        app_config_repository=providers.Factory(create_app_config_repository),
    )
    auth_service = providers.Singleton(
        AuthService,
        uow_factory=uow_factory,
        password_hasher=password_hasher,
        jwt_service=jwt_service,
    )
    audit_service = providers.Singleton(AuditService, uow_factory=uow_factory)
    usage_stats_service = providers.Singleton(UsageStatsService, uow_factory=uow_factory)
    quota_service = providers.Singleton(QuotaService, uow_factory=uow_factory)
    llm_model_service = providers.Singleton(
        LLMModelService,
        uow_factory=uow_factory,
        cipher=cipher,
    )
    skill_service = providers.Singleton(SkillService, uow_factory=uow_factory)
    team_service = providers.Singleton(TeamService, uow_factory=uow_factory)
    service_api_key_service = providers.Singleton(
        ServiceApiKeyService,
        uow_factory=uow_factory,
        hasher=service_api_key_hasher,
    )
    memory_service = providers.Factory(MemoryService, uow_factory=uow_factory)
    llm_token_usage_service = providers.Singleton(
        LLMTokenUsageService,
        uow_factory=uow_factory,
    )
    file_service = providers.Singleton(
        FileService,
        uow_factory=uow_factory,
        file_storage=file_storage,
    )
    session_service = providers.Singleton(
        SessionService,
        uow_factory=uow_factory,
        sandbox_cls=sandbox_cls,
        session_list_notifier=session_list_notifier,
        task_state_port=task_state_port,
    )
    questionnaire_service = providers.Singleton(
        QuestionnaireService,
        uow_factory=uow_factory,
    )
    room_service = providers.Factory(
        RoomService,
        uow_factory=uow_factory,
        redis_client=redis,
    )
    marketplace_service = providers.Factory(
        MarketplaceService,
        llm_model_service=llm_model_service,
        file_service=file_service,
        uow_factory=uow_factory,
    )
    codebase_service = providers.Singleton(
        CodebaseService,
        uow_factory=uow_factory,
        sandbox_cls=sandbox_cls,
        file_storage=file_storage,
    )
    knowledge_base_service = providers.Singleton(
        KnowledgeBaseService,
        uow_factory=uow_factory,
        file_storage=file_storage,
    )

    task_runner_factory = providers.Singleton(
        TaskRunnerFactory,
        uow_factory=uow_factory,
        llm_model_service=llm_model_service,
        skill_service=skill_service,
        memory_service=memory_service,
        sandbox_cls=sandbox_cls,
        json_parser=json_parser,
        search_engine=search_engine,
        file_storage=file_storage,
        config_provider=app_config_provider,
        checkpoint_service=checkpoint_service,
        task_state_port=task_state_port,
        observability_port=observability_port,
        event_sequence_port=event_sequence_port,
        session_state_factory=session_state_factory,
        mcp_connection_pool=mcp_connection_pool,
        a2a_connection_pool=a2a_connection_pool,
    )

    agent_service = providers.Singleton(
        AgentService,
        uow_factory=uow_factory,
        task_cls=task_cls,
        checkpoint_service=checkpoint_service,
        task_state_port=task_state_port,
        event_sequence_port=event_sequence_port,
    )
    a2a_server_service = providers.Singleton(
        A2AServerService,
        agent_service=agent_service,
        session_service=session_service,
        skill_service=skill_service,
        llm_model_service=llm_model_service,
    )


class ApiContainer(BaseContainer):
    """HTTP API composition root: wires FastAPI dependencies, no sandbox pre-warm pool."""

    wiring_config = containers.WiringConfiguration(packages=_WIRE_PACKAGES)

    config_listener = providers.Resource(
        _start_config_listener,
        _=BaseContainer.runtime_configure,
    )


class WorkerContainer(BaseContainer):
    """Agent worker composition root: sandbox pre-warm pool, no HTTP wiring."""

    sandbox_pool_start = providers.Resource(
        _start_sandbox_pool,
        _=BaseContainer.runtime_configure,
    )
    config_listener = providers.Resource(
        _start_config_listener,
        _=sandbox_pool_start,
    )


_api_container: ApiContainer | None = None
_worker_container: WorkerContainer | None = None


def get_api_container() -> ApiContainer:
    global _api_container
    if _api_container is None:
        _api_container = ApiContainer()
    return _api_container


def get_worker_container() -> WorkerContainer:
    global _worker_container
    if _worker_container is None:
        _worker_container = WorkerContainer()
    return _worker_container


async def init_api_container(container: ApiContainer | None = None) -> ApiContainer:
    c = container or get_api_container()
    c.wire(packages=_WIRE_PACKAGES)
    await c.init_resources()
    logger.info("ApiContainer resources initialized")
    return c


async def init_worker_container(container: WorkerContainer | None = None) -> WorkerContainer:
    c = container or get_worker_container()
    await c.init_resources()
    logger.info("WorkerContainer resources initialized")
    return c


async def shutdown_api_container(container: ApiContainer | None = None) -> None:
    c = container or get_api_container()
    try:
        await _stop_config_listener(None)
    except Exception as exc:
        logger.warning("Config listener shutdown failed: %s", exc)
    c.unwire()
    await c.shutdown_resources()
    logger.info("ApiContainer resources shut down")


async def shutdown_worker_container(container: WorkerContainer | None = None) -> None:
    c = container or get_worker_container()
    try:
        await _stop_config_listener(None)
    except Exception as exc:
        logger.warning("Config listener shutdown failed: %s", exc)
    try:
        await _stop_sandbox_pool(None)
    except Exception as exc:
        logger.warning("Sandbox pool shutdown failed: %s", exc)
    await c.shutdown_resources()
    logger.info("WorkerContainer resources shut down")

