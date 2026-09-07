"""Ordered recovery: replay facts and verify decision inputs before release."""

from typing import Protocol
from uuid import UUID

from app.application.ports.execution import FormalProjectorResult
from app.domain.models.scope import OwnerScope


class RecoveryMarker(Protocol):
    async def mark(self, scope: OwnerScope) -> None: ...
    async def clear(self, scope: OwnerScope) -> None: ...


class RecoveryProjector(Protocol):
    async def rebuild(self, scope: OwnerScope) -> FormalProjectorResult: ...


class RecoveryDecisionSource(Protocol):
    async def recover_scope(self, scope: OwnerScope) -> tuple[UUID, ...]: ...


async def rebuild_verified_scope(
    scope: OwnerScope,
    *,
    marker: RecoveryMarker,
    projector: RecoveryProjector,
    source: RecoveryDecisionSource,
) -> tuple[FormalProjectorResult, tuple[UUID, ...]]:
    await marker.mark(scope)
    result = await projector.rebuild(scope)
    recovered = await source.recover_scope(scope)
    await marker.clear(scope)
    return result, recovered
