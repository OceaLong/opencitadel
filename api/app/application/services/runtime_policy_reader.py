"""Process-local, verified Runtime Policy cache backed by PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.domain.execution.commands import normalize_utc
from app.domain.repositories.runtime_policy_repository import RuntimePolicyRepository
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    RuntimePolicyIntegrityError,
    RuntimePolicyPair,
    RuntimePolicyStaleError,
    RuntimePolicyUnavailableError,
)


class RuntimePolicyReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initialized: bool
    ready: bool
    head_version: int | None = None
    last_verified_at: datetime | None = None
    error_key: str | None = None


class PolicyHeadReader(Protocol):
    async def active_execution(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveExecutionPolicy: ...

    async def active_operations(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveOperationsPolicy: ...

    def readiness(self) -> RuntimePolicyReadiness: ...


class OperationsPolicyReader(Protocol):
    async def active_operations(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveOperationsPolicy: ...


class RuntimePolicyReader(PolicyHeadReader):
    def __init__(
        self,
        *,
        repository: RuntimePolicyRepository,
        refresh_interval_seconds: float,
        max_staleness_seconds: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        if max_staleness_seconds <= refresh_interval_seconds:
            raise ValueError("max_staleness_seconds must exceed refresh_interval_seconds")
        self._repository = repository
        self._refresh_interval = timedelta(seconds=refresh_interval_seconds)
        self._max_staleness_seconds = max_staleness_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._pair: RuntimePolicyPair | None = None
        self._last_verified_at: datetime | None = None
        self._next_refresh_at: datetime | None = None
        self._policy_error: RuntimePolicyIntegrityError | RuntimePolicyUnavailableError | None = (
            None
        )
        self._transient_error: BaseException | None = None

    async def initialize(self) -> None:
        now = self._now()
        async with self._lock:
            try:
                pair = await self._repository.load_active_pair()
            except (RuntimePolicyIntegrityError, RuntimePolicyUnavailableError) as exc:
                self._record_policy_or_transient_error(exc, now=now)
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                self._record_transient_error(exc, now=now)
                raise RuntimePolicyUnavailableError(
                    "Runtime Policy PostgreSQL read failed",
                    transient=True,
                ) from exc
            self._accept(pair, now=now)

    async def refresh_if_due(self, *, now: datetime) -> None:
        normalized_now = normalize_utc(now)
        async with self._lock:
            self._require_initialized()
            if self._next_refresh_at is not None and normalized_now < self._next_refresh_at:
                return
            await self._refresh_locked(now=normalized_now)

    async def handle_hint(self) -> None:
        now = self._now()
        async with self._lock:
            self._require_initialized()
            await self._refresh_locked(now=now)

    async def active_execution(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveExecutionPolicy:
        normalized_now = normalize_utc(now)
        if require_fresh:
            # Run admission freezes the execution policy into an immutable
            # snapshot. It must observe PostgreSQL after an acknowledged
            # policy mutation even when the lossy hint has not arrived yet.
            async with self._lock:
                self._require_initialized()
                await self._refresh_locked(now=normalized_now)
        else:
            await self.refresh_if_due(now=normalized_now)
        pair = self._require_initialized()
        self._enforce_freshness(require_fresh=require_fresh, now=normalized_now)
        return pair.execution

    async def active_operations(
        self,
        *,
        require_fresh: bool,
        now: datetime,
    ) -> ActiveOperationsPolicy:
        normalized_now = normalize_utc(now)
        await self.refresh_if_due(now=normalized_now)
        pair = self._require_initialized()
        self._enforce_freshness(require_fresh=require_fresh, now=normalized_now)
        return pair.operations

    def readiness(self) -> RuntimePolicyReadiness:
        now = self._now()
        initialized = self._pair is not None and self._last_verified_at is not None
        error_key: str | None = None
        if self._policy_error is not None:
            error_key = self._policy_error.error_key
        elif self._transient_error is not None:
            error_key = "runtimePolicy.unavailable"
        elif initialized and self._age_seconds(now) > self._max_staleness_seconds:
            error_key = "runtimePolicy.stale"
        return RuntimePolicyReadiness(
            initialized=initialized,
            ready=initialized and error_key is None,
            head_version=self._pair.execution.head.version if self._pair else None,
            last_verified_at=self._last_verified_at,
            error_key=error_key,
        )

    async def _refresh_locked(self, *, now: datetime) -> None:
        try:
            pair = await self._repository.load_active_pair()
        except (RuntimePolicyIntegrityError, RuntimePolicyUnavailableError) as exc:
            self._record_policy_or_transient_error(exc, now=now)
            return
        except (OSError, RuntimeError, ValueError) as exc:
            self._record_transient_error(exc, now=now)
            return
        self._accept(pair, now=now)

    def _accept(self, pair: RuntimePolicyPair, *, now: datetime) -> None:
        self._pair = pair
        self._last_verified_at = now
        self._next_refresh_at = now + self._refresh_interval
        self._policy_error = None
        self._transient_error = None

    def _record_policy_or_transient_error(
        self,
        error: RuntimePolicyIntegrityError | RuntimePolicyUnavailableError,
        *,
        now: datetime,
    ) -> None:
        if isinstance(error, RuntimePolicyUnavailableError) and error.transient:
            self._record_transient_error(error, now=now)
            return
        self._policy_error = error
        self._transient_error = None
        self._next_refresh_at = now + self._refresh_interval

    def _record_transient_error(self, error: BaseException, *, now: datetime) -> None:
        self._transient_error = error
        self._next_refresh_at = now + self._refresh_interval

    def _require_initialized(self) -> RuntimePolicyPair:
        if self._pair is None:
            raise RuntimePolicyUnavailableError("Runtime Policy reader is not initialized")
        return self._pair

    def _enforce_freshness(self, *, require_fresh: bool, now: datetime) -> None:
        if not require_fresh:
            return
        if self._policy_error is not None:
            raise self._policy_error
        age_seconds = self._age_seconds(now)
        if age_seconds > self._max_staleness_seconds:
            raise RuntimePolicyStaleError(age_seconds=age_seconds)

    def _age_seconds(self, now: datetime) -> float:
        if self._last_verified_at is None:
            return float("inf")
        return (now - self._last_verified_at).total_seconds()

    def _now(self) -> datetime:
        return normalize_utc(self._clock())


__all__ = [
    "OperationsPolicyReader",
    "PolicyHeadReader",
    "RuntimePolicyReader",
    "RuntimePolicyReadiness",
]
