"""Application facade for privileged projection rebuilds."""

from __future__ import annotations

from typing import Protocol


class ProjectionRebuildPort(Protocol):
    async def rebuild(self) -> None: ...


class ProjectionRebuilder:
    def __init__(self, rebuild_store: ProjectionRebuildPort) -> None:
        self._rebuild_store = rebuild_store

    async def rebuild(self) -> None:
        await self._rebuild_store.rebuild()
