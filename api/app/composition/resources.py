"""Acquire and release process infrastructure in deterministic order."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.application.ports.coordination import RedisConnectivity
from app.composition.types import ResourceBundle
from app.infrastructure.storage.cos import Cos
from app.infrastructure.storage.minio import Minio
from app.infrastructure.storage.postgres import Postgres
from app.infrastructure.storage.redis import RedisClient
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings

logger = logging.getLogger(__name__)

StorageResource = Cos | Minio


@dataclass(frozen=True)
class ResourceFactories:
    """Pure constructors used by the process resource owner."""

    postgres: Callable[[DeploymentSettings], Postgres]
    redis: Callable[[DeploymentSettings], RedisClient]
    storage: Callable[[DeploymentSettings], StorageResource]


def _new_storage(settings: DeploymentSettings) -> StorageResource:
    provider = (settings.storage_provider or "cos").strip().lower()
    if provider == "minio":
        return Minio(settings)
    return Cos(settings)


DEFAULT_RESOURCE_FACTORIES = ResourceFactories(
    postgres=Postgres,
    redis=RedisClient,
    storage=_new_storage,
)


@asynccontextmanager
async def open_process_resources(
    settings: DeploymentSettings,
    role: ProcessRole,
    *,
    factories: ResourceFactories = DEFAULT_RESOURCE_FACTORIES,
) -> AsyncIterator[ResourceBundle]:
    """Open required resources and keep optional Redis failure explicit."""

    async with AsyncExitStack() as stack:
        postgres = factories.postgres(settings)
        stack.push_async_callback(postgres.shutdown)
        await postgres.init()

        redis = factories.redis(settings)
        stack.push_async_callback(redis.shutdown)
        try:
            await redis.init()
        except (OSError, RedisError, TimeoutError) as exc:
            logger.warning("Redis unavailable during process startup: %s", exc)
            redis_connectivity = RedisConnectivity(
                available=False,
                error_key=type(exc).__name__,
            )
        else:
            redis_connectivity = RedisConnectivity(available=True)

        storage = factories.storage(settings)
        stack.push_async_callback(storage.shutdown)
        await storage.init()

        yield ResourceBundle(
            settings=settings,
            role=role,
            postgres=postgres,
            redis=redis,
            redis_connectivity=redis_connectivity,
            object_storage_client=storage,
            general_redis=redis.client,
        )
