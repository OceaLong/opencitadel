from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.application.ports.coordination import RedisConnectivity
from app.composition.resources import ResourceFactories, open_process_resources
from app.runtime_role import ProcessRole
from core.config import DeploymentSettings

TEST_SETTINGS = DeploymentSettings(env="test")


@dataclass
class _FakePostgres:
    events: list[str]

    @property
    def session_factory(self) -> object:
        return object()

    async def init(self) -> None:
        self.events.append("postgres:start")

    async def shutdown(self) -> None:
        self.events.append("postgres:stop")


@dataclass
class _FakeRedis:
    events: list[str]
    fail: bool = False

    @property
    def client(self) -> object:
        return self

    async def init(self) -> None:
        self.events.append("redis:start")
        if self.fail:
            raise OSError("redis-unavailable")

    async def shutdown(self) -> None:
        self.events.append("redis:stop")


@dataclass
class _FakeStorage:
    events: list[str]
    fail: bool = False

    async def init(self) -> None:
        self.events.append("storage:start")
        if self.fail:
            raise RuntimeError("storage-unavailable")

    async def shutdown(self) -> None:
        self.events.append("storage:stop")


def _factories(
    events: list[str],
    *,
    redis_fails: bool = False,
    storage_fails: bool = False,
) -> ResourceFactories:
    return ResourceFactories(
        postgres=lambda _settings: _FakePostgres(events),
        redis=lambda _settings: _FakeRedis(events, fail=redis_fails),
        storage=lambda _settings: _FakeStorage(events, fail=storage_fails),
    )


@pytest.mark.asyncio
async def test_partial_startup_closes_resources_in_reverse_order() -> None:
    """A later required-resource failure must not leak anything already acquired."""
    events: list[str] = []

    with pytest.raises(RuntimeError, match="storage-unavailable"):
        async with open_process_resources(
            TEST_SETTINGS,
            ProcessRole.API,
            factories=_factories(events, storage_fails=True),
        ):
            pytest.fail("resource context must not yield")

    assert events == [
        "postgres:start",
        "redis:start",
        "storage:start",
        "storage:stop",
        "redis:stop",
        "postgres:stop",
    ]


@pytest.mark.asyncio
async def test_redis_probe_failure_yields_explicit_degraded_resources() -> None:
    """Losing optional Redis must not make PostgreSQL-backed API startup fatal."""
    events: list[str] = []

    async with open_process_resources(
        TEST_SETTINGS,
        ProcessRole.API,
        factories=_factories(events, redis_fails=True),
    ) as resources:
        assert resources.redis_connectivity == RedisConnectivity(
            available=False,
            error_key="OSError",
        )
        assert resources.postgres.session_factory is not None
        assert resources.general_redis is resources.redis.client
        assert resources.role is ProcessRole.API

    assert events == [
        "postgres:start",
        "redis:start",
        "storage:start",
        "storage:stop",
        "redis:stop",
        "postgres:stop",
    ]


@pytest.mark.asyncio
async def test_redis_library_connection_error_is_also_degraded() -> None:
    """The concrete Redis driver's outage exception must follow the optional path."""
    events: list[str] = []

    class DisconnectedRedis(_FakeRedis):
        async def init(self) -> None:
            self.events.append("redis:start")
            raise RedisConnectionError("connection-refused")

    factories = ResourceFactories(
        postgres=lambda _settings: _FakePostgres(events),
        redis=lambda _settings: DisconnectedRedis(events),
        storage=lambda _settings: _FakeStorage(events),
    )

    async with open_process_resources(
        TEST_SETTINGS,
        ProcessRole.API,
        factories=factories,
    ) as resources:
        assert resources.redis_connectivity == RedisConnectivity(
            available=False,
            error_key="ConnectionError",
        )


@pytest.mark.asyncio
async def test_bundle_preserves_the_exact_settings_instance() -> None:
    """Composition must not silently reload settings behind the process boundary."""
    events: list[str] = []

    async with open_process_resources(
        TEST_SETTINGS,
        ProcessRole.EXECUTION_KERNEL,
        factories=_factories(events),
    ) as resources:
        assert resources.settings is TEST_SETTINGS
        assert resources.redis_connectivity == RedisConnectivity(available=True)


@pytest.mark.asyncio
async def test_postgres_initialization_failure_still_closes_partial_resource() -> None:
    """A resource must be owned before init so its partial allocations are cleaned."""
    events: list[str] = []

    class FailingPostgres(_FakePostgres):
        async def init(self) -> None:
            await super().init()
            raise RuntimeError("postgres-unavailable")

    factories = ResourceFactories(
        postgres=lambda _settings: FailingPostgres(events),
        redis=lambda _settings: _FakeRedis(events),
        storage=lambda _settings: _FakeStorage(events),
    )

    with pytest.raises(RuntimeError, match="postgres-unavailable"):
        async with open_process_resources(
            TEST_SETTINGS,
            ProcessRole.API,
            factories=factories,
        ):
            pytest.fail("resource context must not yield")

    assert events == ["postgres:start", "postgres:stop"]


def test_resource_bundle_is_immutable() -> None:
    """Callers must not be able to swap process-owned resources after assembly."""
    from app.composition.types import ResourceBundle

    bundle = ResourceBundle(
        settings=TEST_SETTINGS,
        role=ProcessRole.API,
        postgres=Any,
        redis=Any,
        redis_connectivity=RedisConnectivity(available=True),
        object_storage_client=Any,
        general_redis=Any,
    )

    with pytest.raises(AttributeError):
        bundle.role = ProcessRole.MIGRATE  # type: ignore[misc]
