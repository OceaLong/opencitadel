"""Deliver durable pending Commands into the deterministic orchestrator."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.application.execution.orchestrator import CommandResult
from app.domain.execution.commands import CommandEnvelope


class InboxSource(Protocol):
    async def load_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[CommandEnvelope, ...]: ...


class CommandHandler(Protocol):
    async def handle(self, command: CommandEnvelope) -> CommandResult: ...


class InboxBatchStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    loaded: int
    accepted: int
    rejected: int


class InboxWorker:
    def __init__(self, *, source: InboxSource, handler: CommandHandler) -> None:
        self._source = source
        self._handler = handler

    async def run_once(self, *, now: datetime, limit: int) -> InboxBatchStats:
        if limit <= 0:
            raise ValueError("limit must be positive")
        commands = await self._source.load_pending(now=now, limit=limit)
        accepted = rejected = 0
        for command in commands:
            result = await self._handler.handle(command)
            if result.status == "accepted":
                accepted += 1
            else:
                rejected += 1
        return InboxBatchStats(
            loaded=len(commands),
            accepted=accepted,
            rejected=rejected,
        )


__all__ = ["InboxBatchStats", "InboxWorker"]
