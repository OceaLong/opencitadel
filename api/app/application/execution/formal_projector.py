"""Application facade for formal projection processing."""

from app.application.ports.execution import FormalProjectorPort, FormalProjectorResult
from app.domain.models.scope import OwnerScope


class FormalProjector:
    def __init__(self, projector: FormalProjectorPort) -> None:
        self._projector = projector

    async def run_once(
        self,
        owner_scope: OwnerScope,
        *,
        limit: int,
        through_position: int | None = None,
    ) -> FormalProjectorResult:
        return await self._projector.run_once(
            owner_scope,
            limit=limit,
            through_position=through_position,
        )

    async def rebuild(
        self,
        owner_scope: OwnerScope,
        *,
        through_position: int | None = None,
        batch_size: int = 1000,
    ) -> FormalProjectorResult:
        return await self._projector.rebuild(
            owner_scope,
            through_position=through_position,
            batch_size=batch_size,
        )


__all__ = ["FormalProjector", "FormalProjectorResult"]
