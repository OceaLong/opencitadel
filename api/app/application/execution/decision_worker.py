"""Advance Run workflows from their formal, rebuildable projections."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.execution.decisions import next_command
from app.application.execution.orchestrator import CommandResult
from app.application.execution.run_context import run_execution_context
from app.domain.execution.commands import CommandContext, JsonValue, RegisteredCommand
from app.domain.execution.run import RunState
from core.otel import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("opencitadel.execution.decision")


class DecisionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: RunState
    # Off-stream decision payloads (digest-verified by the source) for the
    # current retry generation, keyed by activity id.
    decision_payloads: dict[UUID, dict[str, JsonValue]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _is_created_and_owned(self) -> DecisionCandidate:
        if self.state.family is None or self.state.correlation_id is None:
            raise ValueError("decision candidate must be a created Run")
        if (self.state.owner_user_id is None) == (self.state.team_id is None):
            raise ValueError("decision candidate requires exactly one owner scope")
        return self


class DecisionSource(Protocol):
    async def load_ready(self, *, limit: int) -> tuple[DecisionCandidate, ...]: ...

    async def disarm(self, run_ids: Collection[UUID]) -> None:
        """Clear the readiness flag for Runs a decision round found idle.

        Without disarming, an idle-but-armed projection row would be reloaded
        and re-decoded on every poll forever (the P0 starvation vector in a
        smaller coat). Sources re-arm rows from new events only.
        """
        ...


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
    # Candidates whose planner raised: isolated, logged, and disarmed rather
    # than aborting the batch.
    errors: int = 0


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
        submitted = rejected = idle = errors = 0
        approval_ttl: int | None = None
        idle_run_ids: list[UUID] = []
        for candidate in candidates:
            state = candidate.state
            with _tracer.start_as_current_span("run.decision") as span:
                run_context = run_execution_context(state)
                span.set_attribute("opencitadel.run_id", str(state.run_id))
                span.set_attribute("opencitadel.correlation_id", str(run_context.correlation_id))
                span.set_attribute("opencitadel.family", str(state.family))
                try:
                    command = next_command(
                        state,
                        run_context,
                        outcomes=candidate.decision_payloads,
                        now=now,
                    )
                except Exception:
                    # One Run's planner defect must not abort the batch (the
                    # same isolation the source applies to undecodable rows) —
                    # an escaped exception here previously wedged the whole
                    # decisions lane once per second. Disarm the Run so it is
                    # not hot-retried; the next real event re-arms it.
                    logger.exception(
                        "decision planner failed run_id=%s family=%s",
                        state.run_id,
                        state.family,
                    )
                    span.set_attribute("opencitadel.decision", "error")
                    errors += 1
                    idle_run_ids.append(state.run_id)
                    continue
                if command is None:
                    span.set_attribute("opencitadel.decision", "idle")
                    idle += 1
                    idle_run_ids.append(state.run_id)
                    continue
                span.set_attribute("opencitadel.command_type", command.command_type)
                if (
                    command.command_type == "RequestApproval"
                    and self._approval_ttl_minutes is not None
                    and command.payload.get("ttl_minutes") is None
                ):
                    if approval_ttl is None:
                        approval_ttl = await self._approval_ttl_minutes(now)
                    # The TTL participates in the command id: two replicas with
                    # differently cached policies then submit two distinct
                    # commands (the loser is rejected by expected_stream_version)
                    # instead of reusing one command_id with two payloads, which
                    # the inbox rejects as an envelope conflict.
                    command = command.model_copy(
                        update={
                            "command_id": uuid5(
                                NAMESPACE_URL,
                                f"opencitadel:{command.command_id}:ttl:{approval_ttl}",
                            ),
                            "payload": {**command.payload, "ttl_minutes": approval_ttl},
                        }
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
        # Disarm the Runs this round found idle (or whose planner errored) so
        # their armed projection rows are not reloaded on every poll.
        if idle_run_ids:
            await self._source.disarm(idle_run_ids)
        return DecisionBatchStats(
            loaded=len(candidates),
            submitted=submitted,
            rejected=rejected,
            idle=idle,
            errors=errors,
        )


__all__ = ["DecisionBatchStats", "DecisionCandidate", "DecisionWorker"]
