"""Application and scheduler contracts for bounded knowledge-version GC."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from app.application.services.resource_version_gc_service import (
    ResourceVersionGCService,
)
from app.domain.repositories.knowledge_version_repository import (
    KnowledgeVersionGCResult,
)
from app.domain.runtime_policy import (
    OperationsPolicy,
    ResourceGcPolicy,
    ResourceVersionGcPolicy,
    SchedulerPolicy,
)
from app.infrastructure.adapters.redis_capabilities import RedisLeaseManager
from app.infrastructure.external.scheduler.job_scheduler import (
    KNOWLEDGE_VERSION_GC_LEASE_KEY,
    run_knowledge_version_gc_tick,
    run_scheduler_loop,
)
from tests.runtime_policy_support import MutablePolicyReader

NOW = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)


class _ExactLeaseRedis:
    """Small Redis lease model with expiry and Lua marker semantics."""

    def __init__(self) -> None:
        self.client = self
        self._values: dict[str, tuple[str, float]] = {}

    def _purge_expired(self, key: str) -> None:
        value = self._values.get(key)
        if value is not None and value[1] <= asyncio.get_running_loop().time():
            self._values.pop(key, None)

    async def set(self, key, token, *, nx=False, px=None, **_kwargs):
        self._purge_expired(key)
        if nx and key in self._values:
            return False
        assert px is not None
        self._values[key] = (
            str(token),
            asyncio.get_running_loop().time() + (int(px) / 1000),
        )
        return True

    async def eval(self, script, key_count, key, token, ttl_ms=None):
        assert key_count == 1
        self._purge_expired(key)
        current = self._values.get(key)
        if "opencitadel:renew-lease" in script:
            assert ttl_ms is not None
            if current is None or current[0] != str(token):
                return 0
            self._values[key] = (
                current[0],
                asyncio.get_running_loop().time() + (int(ttl_ms) / 1000),
            )
            return 1
        if "opencitadel:release-lease" in script:
            assert ttl_ms is None
            if current is None or current[0] != str(token):
                return 0
            self._values.pop(key, None)
            return 1
        raise AssertionError("unexpected lease script")

    def owner(self, key: str) -> str | None:
        self._purge_expired(key)
        value = self._values.get(key)
        return value[0] if value is not None else None


class _GCRepository:
    def __init__(self, results: list[KnowledgeVersionGCResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def collect_garbage(self, **kwargs) -> KnowledgeVersionGCResult:
        self.calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return KnowledgeVersionGCResult()


class _Uow:
    def __init__(self, repository: _GCRepository) -> None:
        self.knowledge_version = repository
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def commit(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1


def _result(*version_ids: str) -> KnowledgeVersionGCResult:
    return KnowledgeVersionGCResult(
        collected_version_ids=tuple(version_ids),
        deleted_versions=len(version_ids),
        deleted_manifests=3,
        deleted_revisions=2,
        reclaimed_logical_bytes=17,
        retained_shared_revisions=1,
        protected_active_versions=1,
        protected_bound_versions=2,
        protected_building_versions=1,
    )


def _job_service():
    return SimpleNamespace(
        reconcile_running_runs=AsyncMock(return_value=0),
    )


def _operations(
    *,
    scheduler_enabled: bool = True,
    gc_enabled: bool = True,
    retain_count: int = 2,
    min_age_days: int = 30,
    batch_size: int = 50,
) -> OperationsPolicy:
    return OperationsPolicy(
        scheduler=SchedulerPolicy(
            enabled=scheduler_enabled,
            poll_interval_seconds=0.1,
            leader_lease_seconds=30,
            max_concurrent_jobs=5,
        ),
        resource_gc=ResourceGcPolicy(
            knowledge_base=ResourceVersionGcPolicy(
                enabled=gc_enabled,
                retention_count=retain_count,
                retention_min_days=min_age_days,
                batch_size=batch_size,
            )
        ),
    )


@pytest.mark.asyncio
async def test_collect_forwards_zero_count_and_age_with_utc_cutoff():
    repository = _GCRepository([_result("old-a", "old-b")])
    uow = _Uow(repository)
    reader = MutablePolicyReader(
        operations=_operations(retain_count=0, min_age_days=0, batch_size=2)
    )
    service = ResourceVersionGCService(
        uow_factory=lambda: uow,
        policy_reader=reader,
        clock=lambda: NOW,
    )

    result = await service.collect_knowledge_versions()

    assert result.collected_version_ids == ("old-a", "old-b")
    assert result.deleted_versions == 2
    assert result.retained_reference_count == 4
    assert repository.calls == [
        {
            "retain_count": 0,
            "older_than": NOW,
            "batch_size": 2,
        }
    ]
    assert uow.entered == uow.exited == 1
    assert reader.operations_calls == [(True, NOW)]


@pytest.mark.asyncio
async def test_disabled_gc_policy_never_opens_a_unit_of_work() -> None:
    factory = Mock()
    service = ResourceVersionGCService(
        uow_factory=factory,
        policy_reader=MutablePolicyReader(operations=OperationsPolicy()),
        clock=lambda: NOW,
    )

    result = await service.collect_knowledge_versions()

    assert result == KnowledgeVersionGCResult()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_collect_uses_created_at_age_cutoff_and_is_repeat_idempotent():
    repository = _GCRepository([_result("expired"), KnowledgeVersionGCResult()])
    service = ResourceVersionGCService(
        uow_factory=lambda: _Uow(repository),
        policy_reader=MutablePolicyReader(operations=_operations()),
        clock=lambda: NOW,
    )

    first = await service.collect_knowledge_versions()
    second = await service.collect_knowledge_versions()

    assert first.collected_version_ids == ("expired",)
    assert second.collected_version_ids == ()
    assert repository.calls[0]["older_than"] == NOW - timedelta(days=30)
    assert repository.calls[1]["older_than"] == NOW - timedelta(days=30)


@pytest.mark.parametrize(
    "payload",
    [
        {"retention_count": -1},
        {"retention_min_days": -1},
        {"batch_size": 0},
        {"batch_size": 501},
    ],
)
def test_invalid_gc_policy_cannot_be_activated(payload):
    with pytest.raises(ValidationError):
        ResourceVersionGcPolicy(**payload)


def test_gc_result_is_frozen_and_metrics_are_deterministic():
    result = _result("b", "a")

    with pytest.raises((AttributeError, TypeError)):
        result.deleted_versions = 99
    assert result.metrics() == {
        "collected_versions": 2,
        "deleted_chunks": 0,
        "deleted_entities": 0,
        "deleted_entity_refs": 0,
        "deleted_manifests": 3,
        "deleted_relations": 0,
        "deleted_revisions": 2,
        "deleted_versions": 2,
        "protected_building_versions": 1,
        "protected_active_versions": 1,
        "protected_age_versions": 0,
        "protected_bound_versions": 2,
        "protected_retention_versions": 0,
        "reclaimed_logical_bytes": 17,
        "retained_reference_count": 4,
        "retained_shared_revisions": 1,
    }


def test_gc_result_rejects_negative_reclaimed_logical_bytes():
    with pytest.raises(
        ValueError,
        match="knowledge-version GC counters must be non-negative",
    ):
        KnowledgeVersionGCResult(reclaimed_logical_bytes=-1)


def test_gc_metrics_expose_byte_count_without_chunk_content():
    chunk_content = "private-token-知识"
    result = KnowledgeVersionGCResult(reclaimed_logical_bytes=len(chunk_content.encode("utf-8")))

    metrics = result.metrics()

    assert metrics["reclaimed_logical_bytes"] == len(chunk_content.encode("utf-8"))
    assert chunk_content not in repr(metrics)
    assert all(isinstance(value, int) for value in metrics.values())


def test_gc_operations_policy_is_default_off_and_bounded():
    config = ResourceVersionGcPolicy()

    assert config.enabled is False
    assert config.retention_count == 10
    assert config.retention_min_days == 30
    assert config.batch_size == 50
    assert (
        ResourceVersionGcPolicy(
            retention_count=0,
            retention_min_days=0,
            batch_size=1,
        ).retention_count
        == 0
    )
    for payload in (
        {"retention_count": -1},
        {"retention_min_days": -1},
        {"batch_size": 0},
        {"batch_size": 501},
    ):
        with pytest.raises(ValidationError):
            ResourceVersionGcPolicy(**payload)


class _SchedulerUow:
    def __init__(self) -> None:
        self.scheduled_job = SimpleNamespace(list_due=AsyncMock(return_value=[]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_scheduler_requires_an_explicit_stop_event_and_lease_manager() -> None:
    import inspect

    parameters = inspect.signature(run_scheduler_loop).parameters

    assert parameters["leases"].default is inspect.Parameter.empty
    assert parameters["stop_event"].default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_disabled_scheduler_stops_without_waiting_for_poll_interval() -> None:
    stop = asyncio.Event()
    policy_reader = MutablePolicyReader(
        operations=_operations(scheduler_enabled=False),
    )
    entered_wait = asyncio.Event()

    async def observe_wait(stopping, seconds):
        assert seconds > 0
        entered_wait.set()
        await stopping.wait()

    with patch(
        "app.infrastructure.external.scheduler.job_scheduler._wait_or_stop",
        side_effect=observe_wait,
    ):
        running = asyncio.create_task(
            run_scheduler_loop(
                lambda: _SchedulerUow(),
                _job_service(),
                leases=SimpleNamespace(),
                worker_id="scheduler-test",
                policy_reader=policy_reader,
                stop_event=stop,
            )
        )
        await asyncio.wait_for(entered_wait.wait(), timeout=1)
        stop.set()
        await asyncio.wait_for(running, timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduler_enabled", "gc_enabled", "expected_gc_calls"),
    [
        (False, True, 0),
        (True, False, 0),
        (True, True, 1),
    ],
)
async def test_scheduler_respects_global_and_gc_disable_gates(
    scheduler_enabled,
    gc_enabled,
    expected_gc_calls,
):
    stop = asyncio.Event()
    gc_service = SimpleNamespace(collect_knowledge_versions=AsyncMock(return_value=_result("old")))
    leader = AsyncMock(return_value=True)
    policy_reader = MutablePolicyReader(
        operations=_operations(
            scheduler_enabled=scheduler_enabled,
            gc_enabled=gc_enabled,
        )
    )

    async def run_gc_tick(service, **_kwargs):
        return await service.collect_knowledge_versions()

    gc_tick = AsyncMock(side_effect=run_gc_tick)

    async def stop_after_tick(_stopping, _seconds):
        stop.set()

    with (
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.try_become_scheduler_leader",
            leader,
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler._wait_or_stop",
            side_effect=stop_after_tick,
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.run_knowledge_version_gc_tick",
            gc_tick,
        ),
    ):
        await run_scheduler_loop(
            lambda: _SchedulerUow(),
            _job_service(),
            leases=SimpleNamespace(),
            worker_id="scheduler-test",
            policy_reader=policy_reader,
            resource_version_gc_service=gc_service,
            stop_event=stop,
        )

    assert gc_service.collect_knowledge_versions.await_count == expected_gc_calls
    assert gc_tick.await_count == expected_gc_calls
    if not scheduler_enabled:
        leader.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_logs_gc_failure_and_continues_scheduled_jobs():
    stop = asyncio.Event()
    gc_service = SimpleNamespace(
        collect_knowledge_versions=AsyncMock(side_effect=RuntimeError("injected GC failure"))
    )
    uow = _SchedulerUow()
    policy_reader = MutablePolicyReader(operations=_operations())

    async def run_gc_tick(service, **_kwargs):
        return await service.collect_knowledge_versions()

    gc_tick = AsyncMock(side_effect=run_gc_tick)

    async def stop_after_tick(_stopping, _seconds):
        stop.set()

    with (
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.try_become_scheduler_leader",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler._wait_or_stop",
            side_effect=stop_after_tick,
        ),
        patch("app.infrastructure.external.scheduler.job_scheduler.logger") as logger,
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.run_knowledge_version_gc_tick",
            gc_tick,
        ),
    ):
        await run_scheduler_loop(
            lambda: uow,
            _job_service(),
            leases=SimpleNamespace(),
            worker_id="scheduler-test",
            policy_reader=policy_reader,
            resource_version_gc_service=gc_service,
            stop_event=stop,
        )

    logger.exception.assert_called_once()
    uow.scheduled_job.list_due.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_non_leader_never_runs_gc():
    stop = asyncio.Event()
    gc_service = SimpleNamespace(collect_knowledge_versions=AsyncMock())
    policy_reader = MutablePolicyReader(operations=_operations())

    async def stop_after_tick(_stopping, _seconds):
        stop.set()

    with (
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.try_become_scheduler_leader",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler._wait_or_stop",
            side_effect=stop_after_tick,
        ),
    ):
        await run_scheduler_loop(
            lambda: _SchedulerUow(),
            _job_service(),
            leases=SimpleNamespace(),
            worker_id="scheduler-test",
            policy_reader=policy_reader,
            resource_version_gc_service=gc_service,
            stop_event=stop,
        )

    gc_service.collect_knowledge_versions.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_owner_cannot_renew_or_release_new_owner_lease():
    redis = _ExactLeaseRedis()
    leases = RedisLeaseManager(redis)
    assert await leases.acquire("lease:test", "worker-old", ttl_seconds=0.05)
    await asyncio.sleep(0.07)
    assert await leases.acquire("lease:test", "worker-new", ttl_seconds=0.2)

    assert not await leases.renew("lease:test", "worker-old", ttl_seconds=0.2)
    assert not await leases.release("lease:test", "worker-old")
    assert redis.owner("lease:test") == "worker-new"


@pytest.mark.asyncio
async def test_two_workers_cannot_overlap_gc_past_original_lease_expiry():
    redis = _ExactLeaseRedis()
    leases = RedisLeaseManager(redis)
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def collect_first(*_args):
        first_started.set()
        await release_first.wait()
        return _result("old-a")

    first_service = SimpleNamespace(collect_knowledge_versions=AsyncMock(side_effect=collect_first))
    second_service = SimpleNamespace(
        collect_knowledge_versions=AsyncMock(return_value=_result("old-b"))
    )
    first_tick = asyncio.create_task(
        run_knowledge_version_gc_tick(
            first_service,
            leases=leases,
            worker_id="worker-a",
            lease_seconds=0.12,
            owner_token="worker-a:tick-1",
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    # Exceed the lease acquired at tick start. The keepalive must retain
    # ownership for worker A throughout the still-running collection.
    await asyncio.sleep(0.2)
    second = await run_knowledge_version_gc_tick(
        second_service,
        leases=leases,
        worker_id="worker-b",
        lease_seconds=0.12,
        owner_token="worker-b:tick-1",
    )

    assert second is None
    second_service.collect_knowledge_versions.assert_not_awaited()
    assert redis.owner(KNOWLEDGE_VERSION_GC_LEASE_KEY) == ("worker-a:tick-1")
    release_first.set()
    first = await asyncio.wait_for(first_tick, timeout=1)

    assert first.collected_version_ids == ("old-a",)
    assert redis.owner(KNOWLEDGE_VERSION_GC_LEASE_KEY) is None
