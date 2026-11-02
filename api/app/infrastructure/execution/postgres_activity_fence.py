"""Transaction-local authority for worker results and durable timeout commands.

The caller acquires the scope append lock first. The task row stays locked until
the event, operational state and inbox result commit in the same transaction.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.commands import CommandEnvelope, normalize_utc
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionScheduledCommandORM,
)
from app.infrastructure.observability.execution_metrics import record_activity_lease_event

WORKER_ACTIVITY_COMMANDS = frozenset(
    {"MarkActivityCallStarted", "CompleteActivity", "FailActivity", "MarkActivityOutcomeUnknown"}
)
_FENCES_KEY = "execution_activity_command_fences"


class ActivityLeaseExpiredError(RuntimeError):
    """The accepting transaction must roll back: its worker lease expired."""


@dataclass(frozen=True)
class _Fence:
    transaction: object
    deadline: datetime


async def _now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise TypeError("database did not return its current lease clock")
    return normalize_utc(value)


async def authorize_activity_command(session: AsyncSession, command: CommandEnvelope) -> bool:
    """Authorize one worker submission after the caller's scope append lock.

    Effects and reconciler commands use their own operation/request authorization;
    they must not borrow an expired worker lease to settle an unknown outcome.
    """
    if command.command_type not in WORKER_ACTIVITY_COMMANDS:
        return True
    try:
        activity_id = UUID(str(command.payload["activity_id"]))
        generation = command.payload["generation"]
    except (KeyError, ValueError, TypeError):
        return False
    if type(generation) is not int or generation < 0:
        return False
    task = await session.scalar(
        select(ExecutionActivityTaskORM)
        .where(
            ExecutionActivityTaskORM.activity_id == activity_id,
            ExecutionActivityTaskORM.aggregate_type == command.stream_type,
            ExecutionActivityTaskORM.aggregate_id == command.stream_id,
            ExecutionActivityTaskORM.request_generation == generation,
            ExecutionActivityTaskORM.owner_user_id == command.owner_user_id,
            ExecutionActivityTaskORM.team_id == command.team_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await _now(session)
    if task is None:
        if command.activity_lease_proof is not None:
            record_activity_lease_event("settlement_rejected")
        return False
    proof = command.activity_lease_proof
    if proof is None:
        return await _trusted_timeout_or_dead_letter(session, command, task, now)
    if (
        proof.activity_id != activity_id
        or proof.request_generation != generation
        or command.payload.get("claim_generation") != proof.claim_generation
        or proof.claim_generation != task.claim_generation
        or proof.worker_id != task.claimed_by
        or task.status not in {"claimed", "call_started"}
        or task.claim_deadline is None
        or task.claim_deadline <= now
        or task.timeout_at <= now
    ):
        record_activity_lease_event("settlement_rejected")
        return False
    session.info.setdefault(_FENCES_KEY, {})[command.command_id] = _Fence(
        transaction=session.sync_session.get_transaction(),
        deadline=min(task.claim_deadline, task.timeout_at),
    )
    return True


async def ensure_activity_lease_current(session: AsyncSession, command: CommandEnvelope) -> None:
    """Check again immediately before commit, after all accepting writes.

    Settlement clears the task lease within this transaction. Its original row lock
    still excludes a transfer, so the saved deadline is the authority at this point.
    """
    if command.command_type not in WORKER_ACTIVITY_COMMANDS or command.activity_lease_proof is None:
        return
    fence = session.info.get(_FENCES_KEY, {}).get(command.command_id)
    if (
        fence is None
        or fence.transaction is not session.sync_session.get_transaction()
        or fence.deadline <= await _now(session)
    ):
        record_activity_lease_event("settlement_rejected")
        raise ActivityLeaseExpiredError("activity lease expired before settlement commit")


async def _trusted_timeout_or_dead_letter(session, command, task, now) -> bool:
    """Internal command names alone never grant timer or dead-letter authority."""
    if command.payload.get("claim_generation") is not None:
        return False
    if (
        command.command_type == "FailActivity"
        and command.payload.get("failure_code") == "ACTIVITY_DEAD_LETTERED"
    ):
        return (
            command.command_id
            == uuid5(
                NAMESPACE_URL,
                f"opencitadel:activity-dead-letter:{task.activity_id}:{task.request_generation}",
            )
            and task.status == "dead_lettered"
            and task.failure_code == "ACTIVITY_DEAD_LETTERED"
            and task.call_started_at is None
            and task.completed_at is not None
            and task.completed_at <= now
            and command.causation_id == task.activity_id
            and command.correlation_id == task.activity_id
        )
    if task.timeout_at > now:
        return False
    timer_id = uuid5(
        NAMESPACE_URL,
        f"opencitadel:activity-timeout:{task.activity_id}:{task.request_generation}",
    )
    # No timer lock: cancellation obtains task -> timer in this transaction, and
    # the dispatcher obtains timer -> inbox without ever locking a task row.
    timer = await session.get(ExecutionScheduledCommandORM, timer_id)
    if (
        timer is None
        or timer.status != "fired"
        or timer.fired_at is None
        or timer.due_at != task.timeout_at
        or timer.due_at > now
        or timer.owner_user_id != command.owner_user_id
        or timer.team_id != command.team_id
        or timer.cancellation_activity_id != task.activity_id
        or timer.cancellation_event_id is not None
    ):
        return False
    original = CommandEnvelope.model_validate(timer.command_envelope)
    if command.command_type == "FailActivity":
        return command == original.model_copy(
            update={"command_id": uuid5(NAMESPACE_URL, f"opencitadel:timer:{timer.timer_id}")}
        )
    return (
        command.command_type == "MarkActivityOutcomeUnknown"
        and command.command_id
        == uuid5(
            NAMESPACE_URL,
            f"opencitadel:timeout-unknown:{task.aggregate_id}:{task.activity_id}:{task.request_generation}",
        )
        and command.payload
        == {
            "activity_id": str(task.activity_id),
            "generation": task.request_generation,
            "failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN",
        }
        and command.issued_at == original.issued_at
        and command.causation_id is None
        and command.correlation_id == UUID(task.aggregate_id)
        and task.call_started_at is not None
    )


__all__ = [
    "WORKER_ACTIVITY_COMMANDS",
    "ActivityLeaseExpiredError",
    "authorize_activity_command",
    "ensure_activity_lease_current",
]
