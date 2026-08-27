from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from app.application.services.auth_service import AuthService
from app.application.services.notification_service import NotificationService
from app.application.services.runtime_policy_service import RuntimePolicyService
from app.application.services.session_service import SessionService
from app.application.services.status_service import StatusService
from app.composition.api import open_api_runtime
from app.composition.resources import ResourceFactories
from app.domain.runtime_policy import RuntimePolicyPair
from core.config import DeploymentSettings
from tests.runtime_policy_support import MutablePolicyReader

TEST_SETTINGS = DeploymentSettings(
    env="test",
    policy_head_refresh_interval_seconds=5,
    policy_max_staleness_seconds=30,
)


class _PolicyRepository:
    def __init__(self) -> None:
        policy = MutablePolicyReader()
        self.pair = RuntimePolicyPair(
            execution=policy.execution,
            operations=policy.operations,
        )

    async def load_active_pair(self) -> RuntimePolicyPair:
        return self.pair


@dataclass
class _FakePostgres:
    events: list[str]

    @property
    def session_factory(self):
        def unopened_session():
            raise AssertionError("runtime construction must not open a database session")

        return unopened_session

    async def init(self) -> None:
        self.events.append("postgres:start")

    async def shutdown(self) -> None:
        self.events.append("postgres:stop")


@dataclass
class _FakeRedis:
    events: list[str]
    available: bool = False
    pubsubs: list[_FakePubSub] = field(default_factory=list)

    @property
    def client(self):
        return self

    async def init(self) -> None:
        self.events.append("redis:start")
        if not self.available:
            raise OSError("redis-unavailable")

    async def shutdown(self) -> None:
        self.events.append("redis:stop")

    async def set(self, *_args, **_kwargs):
        raise OSError("redis-unavailable")

    async def eval(self, *_args, **_kwargs):
        raise OSError("redis-unavailable")

    def pubsub(self) -> _FakePubSub:
        pubsub = _FakePubSub(self.events)
        self.pubsubs.append(pubsub)
        return pubsub


@dataclass
class _FakePubSub:
    events: list[str]

    async def subscribe(self, channel: str) -> None:
        self.events.append(f"pubsub:subscribe:{channel}")

    async def get_message(self, **_kwargs):
        await asyncio.Event().wait()

    async def unsubscribe(self, channel: str) -> None:
        self.events.append(f"pubsub:unsubscribe:{channel}")

    async def aclose(self) -> None:
        self.events.append("pubsub:stop")


@dataclass
class _FakeStorage:
    events: list[str]

    async def init(self) -> None:
        self.events.append("storage:start")

    async def shutdown(self) -> None:
        self.events.append("storage:stop")


def _resource_factories(events: list[str]) -> ResourceFactories:
    return ResourceFactories(
        postgres=lambda _settings: _FakePostgres(events),
        redis=lambda _settings: _FakeRedis(events),
        storage=lambda _settings: _FakeStorage(events),
    )


@pytest.mark.asyncio
async def test_api_runtime_builds_complete_graph_without_kernel_workers() -> None:
    """API composition must expose its graph without accidentally owning workers."""
    events: list[str] = []
    repository = _PolicyRepository()

    async with open_api_runtime(
        TEST_SETTINGS,
        factories=_resource_factories(events),
        runtime_policy_repository_factory=lambda _resources: repository,
    ) as runtime:
        assert isinstance(runtime.auth_service, AuthService)
        assert isinstance(runtime.session_service, SessionService)
        assert isinstance(runtime.runtime_policy_service, RuntimePolicyService)
        assert isinstance(runtime.notification_service, NotificationService)
        assert isinstance(runtime.status_service, StatusService)
        assert runtime.auth_service._uow_factory is runtime.uow_factory
        assert runtime.session_service._uow_factory is runtime.uow_factory
        assert runtime.uow_factory()._secret_cipher is runtime.secret_cipher
        assert runtime.inference_endpoint_service._cipher is runtime.secret_cipher
        assert runtime.sandbox_factory._operations is runtime.runtime_policy_reader
        assert runtime.rate_limit_store._redis is runtime.resources.general_redis
        assert runtime.application_urls.frontend_base_url == TEST_SETTINGS.frontend_base_url
        assert runtime.application_urls.oauth_redirect_base == TEST_SETTINGS.oauth_redirect_base
        assert runtime.runtime_policy_reader.readiness().ready is True
        assert runtime.readiness.ready is True
        assert not hasattr(runtime, "execution_kernel")
        assert not hasattr(runtime, "scheduler_loop")
        assert runtime.supervisor.pending_names == ()

        with pytest.raises(FrozenInstanceError):
            runtime.auth_service = object()  # type: ignore[misc]

    assert runtime.readiness.ready is False
    assert runtime.supervisor.pending_names == ()
    assert events == [
        "postgres:start",
        "redis:start",
        "storage:start",
        "storage:stop",
        "redis:stop",
        "postgres:stop",
    ]


@pytest.mark.asyncio
async def test_api_runtime_rejects_policy_startup_failure_and_unwinds_resources() -> None:
    """An unavailable PostgreSQL policy head is a hard startup dependency."""
    events: list[str] = []

    class BrokenRepository(_PolicyRepository):
        async def load_active_pair(self) -> RuntimePolicyPair:
            raise RuntimeError("policy-read-failed")

    with pytest.raises(Exception, match="Runtime Policy PostgreSQL read failed"):
        async with open_api_runtime(
            TEST_SETTINGS,
            factories=_resource_factories(events),
            runtime_policy_repository_factory=lambda _resources: BrokenRepository(),
        ):
            pytest.fail("runtime context must not yield")

    assert events[-3:] == ["storage:stop", "redis:stop", "postgres:stop"]


@pytest.mark.asyncio
async def test_available_redis_listener_is_supervised_and_closed_before_resources() -> None:
    """The API-owned policy stream must be cancelled before its Redis client closes."""
    events: list[str] = []
    redis = _FakeRedis(events, available=True)
    factories = ResourceFactories(
        postgres=lambda _settings: _FakePostgres(events),
        redis=lambda _settings: redis,
        storage=lambda _settings: _FakeStorage(events),
    )

    async with open_api_runtime(
        TEST_SETTINGS,
        factories=factories,
        runtime_policy_repository_factory=lambda _resources: _PolicyRepository(),
    ) as runtime:
        assert runtime.supervisor.pending_names == ("runtime-policy-hints",)
        assert events[-1] == "pubsub:subscribe:runtime_policy:changed"

    assert runtime.supervisor.pending_names == ()
    assert events[-5:] == [
        "pubsub:unsubscribe:runtime_policy:changed",
        "pubsub:stop",
        "storage:stop",
        "redis:stop",
        "postgres:stop",
    ]
