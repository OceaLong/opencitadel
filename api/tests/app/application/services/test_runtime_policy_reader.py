from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
    RuntimePolicyIntegrityError,
    RuntimePolicyPair,
    RuntimePolicyStaleError,
    RuntimePolicyUnavailableError,
    TrafficPolicy,
    policy_digest,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 26, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _PolicyRepository:
    def __init__(self, pair: RuntimePolicyPair) -> None:
        self.pair = pair
        self.error: Exception | None = None
        self.calls = 0

    async def load_active_pair(self) -> RuntimePolicyPair:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.pair


def _pair(*, version: int, requests_per_minute: int) -> RuntimePolicyPair:
    execution_id = uuid4()
    operations_id = uuid4()
    execution = ExecutionPolicy()
    operations = OperationsPolicy(traffic=TrafficPolicy(requests_per_minute=requests_per_minute))
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    head = RuntimePolicyHead(
        version=version,
        execution_revision_id=execution_id,
        operations_revision_id=operations_id,
        updated_by="admin-1",
        updated_at=now,
    )
    return RuntimePolicyPair(
        execution=ActiveExecutionPolicy(
            head=head,
            revision=ExecutionPolicyRevision(
                id=execution_id,
                sequence=version,
                schema_version=1,
                policy=execution,
                digest=policy_digest(1, execution),
                created_by="admin-1",
                note=f"execution {version}",
                created_at=now,
            ),
        ),
        operations=ActiveOperationsPolicy(
            head=head,
            revision=OperationsPolicyRevision(
                id=operations_id,
                sequence=version,
                schema_version=1,
                policy=operations,
                digest=policy_digest(1, operations),
                created_by="admin-1",
                note=f"operations {version}",
                created_at=now,
            ),
        ),
    )


def _reader(repository: _PolicyRepository, clock: _Clock):
    from app.application.services.runtime_policy_reader import RuntimePolicyReader

    return RuntimePolicyReader(
        repository=repository,
        refresh_interval_seconds=5,
        max_staleness_seconds=30,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_dropped_hint_converges_by_postgres_poll() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    reader = _reader(repository, clock)
    await reader.initialize()

    repository.pair = _pair(version=2, requests_per_minute=7)
    clock.advance(seconds=5)

    active = await reader.active_operations(require_fresh=True, now=clock())

    assert active.revision.policy.traffic.requests_per_minute == 7
    assert repository.calls == 2
    assert reader.readiness().ready is True


@pytest.mark.asyncio
async def test_hint_forces_refresh_before_poll_deadline() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    reader = _reader(repository, clock)
    await reader.initialize()

    repository.pair = _pair(version=2, requests_per_minute=9)
    await reader.handle_hint()

    active = await reader.active_operations(require_fresh=True, now=clock())

    assert active.revision.policy.traffic.requests_per_minute == 9
    assert repository.calls == 2


@pytest.mark.asyncio
async def test_fresh_execution_read_bypasses_poll_interval_for_run_admission() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    reader = _reader(repository, clock)
    await reader.initialize()

    repository.pair = _pair(version=2, requests_per_minute=120)

    active = await reader.active_execution(require_fresh=True, now=clock())

    assert active.head.version == 2
    assert repository.calls == 2


@pytest.mark.asyncio
async def test_transient_database_failure_keeps_last_pair_until_stale() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    reader = _reader(repository, clock)
    await reader.initialize()
    repository.error = OSError("database unavailable")

    clock.advance(seconds=5)
    still_verified = await reader.active_operations(require_fresh=True, now=clock())
    assert still_verified.head.version == 1
    assert reader.readiness().ready is False

    clock.advance(seconds=26)
    with pytest.raises(RuntimePolicyStaleError):
        await reader.active_operations(require_fresh=True, now=clock())

    diagnostic = await reader.active_operations(require_fresh=False, now=clock())
    assert diagnostic.head.version == 1


@pytest.mark.asyncio
async def test_invalid_new_head_fails_fresh_reads_immediately() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    reader = _reader(repository, clock)
    await reader.initialize()
    repository.error = RuntimePolicyIntegrityError("digest mismatch")
    clock.advance(seconds=5)

    with pytest.raises(RuntimePolicyIntegrityError, match="digest mismatch"):
        await reader.active_execution(require_fresh=True, now=clock())

    diagnostic = await reader.active_execution(require_fresh=False, now=clock())
    assert diagnostic.head.version == 1
    assert reader.readiness().ready is False
    assert reader.readiness().error_key == "runtimePolicy.integrity"


@pytest.mark.asyncio
async def test_initialization_fails_closed_without_policy_head() -> None:
    clock = _Clock()
    repository = _PolicyRepository(_pair(version=1, requests_per_minute=120))
    repository.error = RuntimePolicyUnavailableError("head missing")
    reader = _reader(repository, clock)

    with pytest.raises(RuntimePolicyUnavailableError, match="head missing"):
        await reader.initialize()

    assert reader.readiness().initialized is False
    assert reader.readiness().ready is False
