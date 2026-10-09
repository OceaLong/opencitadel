"""Storage contracts for scoped, immutable execution read cuts."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope


class ViewNotFound(LookupError):
    code = "not_found"


class ViewCursorInvalid(ValueError):
    code = "invalid_argument"


class ViewRevisionExpired(ValueError):
    code = "revision_conflict"


class ViewRebuilding(RuntimeError):
    code = "projection_rebuilding"


@dataclass(frozen=True)
class ScopeViewHead:
    """A scope audit summary, not a substitute for each Run's exact boundary."""

    formal_position: int
    observed_count: int
    run_count: int


@dataclass(frozen=True)
class ShadowResult:
    generation: str
    algorithm_version: int
    source_version: int
    captured_runs: int
    caught_up_runs: int
    activated: bool
    captured_head: ScopeViewHead
    caught_up_head: ScopeViewHead


class ExecutionViewPort(Protocol):
    async def production_observation(
        self, session, scope, boundary, event_id, formal_position
    ) -> dict | None: ...

    """Opaque session handle; concrete public result records cross this seam."""

    def transaction(self, *, writable: bool = False) -> AbstractAsyncContextManager[Any]: ...
    async def capture_run(
        self, session: Any, scope: OwnerScope, run_id: UUID
    ) -> PlaybackBoundary: ...
    async def active_generation(self, session: Any, scope: OwnerScope) -> str: ...
    async def restore(
        self, session: Any, scope: OwnerScope, boundary: PlaybackBoundary, generation: str
    ) -> RestoredState: ...
    async def prepare_read(
        self, session: Any, scope: OwnerScope, boundary: PlaybackBoundary, generation: str
    ) -> ReadCut: ...
    async def page_steps(
        self,
        session: Any,
        scope: OwnerScope,
        boundary: PlaybackBoundary,
        storage: StepStorage,
        filters: dict[str, str],
        after: tuple[int, str] | None,
        limit: int,
    ) -> StoredStepPage: ...
    async def point_step(
        self,
        session: Any,
        scope: OwnerScope,
        boundary: PlaybackBoundary,
        storage: StepStorage,
        step_id: str,
    ) -> StoredStep | None: ...
    async def first_replayable(
        self, session: Any, scope: OwnerScope, boundary: PlaybackBoundary
    ) -> PlaybackBoundary | None: ...
    async def capture_cohort(
        self, session: Any, scope: OwnerScope, filters: dict[str, Any], generation: str
    ) -> str: ...
    async def cohort_page(
        self,
        session: Any,
        scope: OwnerScope,
        cohort_id: str,
        after: tuple[str | None, str] | None,
        limit: int,
    ) -> list[CohortRun]: ...
    async def timeline(
        self,
        session: Any,
        scope: OwnerScope,
        boundary: PlaybackBoundary,
        start: datetime,
        end: datetime,
        target: datetime | None,
        direction: Literal["before", "after"],
        count: int,
        anchor_order: int | None = None,
    ) -> StoredTimeline: ...


@dataclass(frozen=True)
class StepStorage:
    kind: Literal["shadow", "cache"]
    identity: str


@dataclass(frozen=True)
class ReadCut:
    state: dict[str, Any]
    missing_intervals: tuple[dict[str, Any], ...]
    steps: StepStorage


@dataclass(frozen=True)
class StoredStep:
    step_id: str
    observed_order: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class StoredStepPage:
    rows: list[StoredStep]
    hidden_count: int


@dataclass(frozen=True)
class CohortRun:
    run_id: UUID
    admitted_at: datetime | None
    boundary: dict[str, Any]


@dataclass(frozen=True)
class StoredTimelineBucket:
    start: datetime
    end: datetime
    count: int
    formal_count: int
    first: dict[str, Any]
    last: dict[str, Any]


@dataclass(frozen=True)
class StoredKeyEvent:
    boundary: dict[str, Any]
    kinds: list[str]


@dataclass(frozen=True)
class StoredTimeline:
    buckets: list[StoredTimelineBucket]
    selected: dict[str, Any] | None
    key_events: list[StoredKeyEvent]


@dataclass(frozen=True)
class RestoredState:
    state: dict[str, Any]
    missing_intervals: tuple[dict[str, Any], ...]
