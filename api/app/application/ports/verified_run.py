"""Scoped, verified recovery; the caller owns its transaction and locks."""

from dataclasses import dataclass
from typing import Protocol

from app.domain.execution.aggregate import ReplayResult
from app.domain.execution.definition import DefinitionPin
from app.domain.execution.run import RunState


@dataclass(frozen=True)
class VerifiedRun:
    replay: ReplayResult[RunState]
    snapshot_version: int | None
    tail_count: int
    snapshot_bytes: int


class VerifiedRunLoaderPort(Protocol):
    async def load(
        self,
        run_id: str,
        *,
        owner_scope: tuple[str | None, str | None],
        required_pin: DefinitionPin | None = None,
        at_version: int | None = None,
    ) -> VerifiedRun: ...
