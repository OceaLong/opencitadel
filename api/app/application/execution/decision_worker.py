"""Advance Run workflows from their formal, rebuildable projections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from app.application.execution.decisions import next_command
from app.application.execution.orchestrator import CommandResult
from app.application.execution.run_context import run_execution_context
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.run import RunState
from app.observability.otel import get_tracer

_tracer = get_tracer("opencitadel.execution.decision")


class DecisionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: RunState

    @model_validator(mode="after")
    def _is_created_and_owned(self) -> DecisionCandidate:
        if self.state.family is None or self.state.correlation_id is None:
            raise ValueError("decision candidate must be a created Run")
        if (self.state.owner_user_id is None) == (self.state.team_id is None):
            raise ValueError("decision candidate requires exactly one owner scope")
        return self


class DecisionSource(Protocol):
    async def load_ready(self, *, limit: int) -> tuple[DecisionCandidate, ...]: ...


class RunCommandService(Protocol):
    async def submit(
        self,
        command: RegisteredCommand,
        context: CommandContext,
    ) -> CommandResult: ...


class DecisionBatchStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    loaded: int = 0
    submitted: int = 0
    rejected: int = 0
    idle: int = 0


class DecisionWorker:
    def __init__(
        self,
        *,
        source: DecisionSource,
        run_service: RunCommandService,
        approval_ttl_minutes: Callable[[datetime], Awaitable[int]] | None = None,
    ) -> None:
        self._source = source
        self._run_service = run_service
        # Operations-policy approval TTL, injected into RequestApproval payloads
        # at submit time so tenants can override the aggregate's fixed default.
        self._approval_ttl_minutes = approval_ttl_minutes

    async def run_once(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> DecisionBatchStats:
        if limit <= 0:
            raise ValueError("limit must be positive")
        candidates = await self._source.load_ready(limit=limit)
        submitted = rejected = idle = 0
        approval_ttl: int | None = None
        for candidate in candidates:
            state = candidate.state
            with _tracer.start_as_current_span("run.decision") as span:
                run_context = run_execution_context(state)
                span.set_attribute("opencitadel.run_id", str(state.run_id))
                span.set_attribute("opencitadel.correlation_id", str(run_context.correlation_id))
                span.set_attribute("opencitadel.family", str(state.family))
                command = next_command(
                    state,
                    run_context,
                    now=now,
                )
                if command is None:
                    span.set_attribute("opencitadel.decision", "idle")
                    idle += 1
                    continue
                span.set_attribute("opencitadel.command_type", command.command_type)
                if (
                    command.command_type == "RequestApproval"
                    and self._approval_ttl_minutes is not None
                    and command.payload.get("ttl_minutes") is None
                ):
                    if approval_ttl is None:
                        approval_ttl = await self._approval_ttl_minutes(now)
                    command = command.model_copy(
                        update={"payload": {**command.payload, "ttl_minutes": approval_ttl}}
                    )
                result = await self._run_service.submit(
                    command,
                    CommandContext(
                        owner_user_id=state.owner_user_id,
                        team_id=state.team_id,
                        correlation_id=run_context.correlation_id,
                        causation_id=None,
                        issued_at=now,
                    ),
                )
                span.set_attribute("opencitadel.decision", result.status)
                if result.status == "accepted":
                    submitted += 1
                else:
                    rejected += 1
        return DecisionBatchStats(
            loaded=len(candidates),
            submitted=submitted,
            rejected=rejected,
            idle=idle,
        )


__all__ = ["DecisionBatchStats", "DecisionCandidate", "DecisionWorker"]
