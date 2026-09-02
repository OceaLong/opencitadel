"""Workflow dispatch with universal stream invariants."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .commands import CommandEnvelope
from .decisions import Decision, DecisionFacts, DecisionRejected
from .events import NewEvent
from .state import RunState
from .types import RunStatus, Workflow

WorkflowReducer = Callable[[RunState | None, CommandEnvelope, DecisionFacts], Decision]


class ReducerError(DecisionRejected):
    """Base class for deterministic command rejection."""


class UnknownWorkflow(ReducerError):
    code = "unknown_workflow"


class OwnerScopeConflict(ReducerError):
    code = "owner_scope_conflict"


class CommandVersionConflict(ReducerError):
    code = "command_version_conflict"


class WorkflowConflict(ReducerError):
    code = "workflow_conflict"


class LifecycleConflict(ReducerError):
    code = "run_lifecycle_conflict"


def _event(
    facts: DecisionFacts,
    index: int,
    type_: str,
    public: dict[str, Any] | None = None,
) -> NewEvent:
    try:
        event_id = facts.event_ids[index]
    except IndexError as exc:
        raise LifecycleConflict(f"missing event id at decision index {index}") from exc
    return NewEvent(
        event_id=event_id,
        type=type_,
        public_payload=public or {},
        occurred_at=facts.now,
    )


def _lifecycle_decision(
    state: RunState,
    command: CommandEnvelope,
    facts: DecisionFacts,
) -> Decision | None:
    if command.type == "ArchiveRun":
        if state.status in {RunStatus.ARCHIVED, RunStatus.PURGED}:
            raise LifecycleConflict("Run is already archived or purged")
        purge_after = command.payload.get("purge_after")
        if purge_after is None:
            raise LifecycleConflict("archive requires purge_after")
        events: list[NewEvent] = []
        for effect_id in state.active_effect_ids:
            events.append(
                _event(
                    facts,
                    len(events),
                    "EffectCancelled",
                    {"effect_id": str(effect_id)},
                )
            )
        for approval in state.pending_approvals:
            events.append(
                _event(
                    facts,
                    len(events),
                    "ApprovalCancelled",
                    {
                        "approval_id": str(approval.approval_id),
                        "effect_id": str(approval.effect_id),
                        "timer_id": str(approval.timer_id),
                    },
                )
            )
        events.append(
            _event(
                facts,
                len(events),
                "RunArchived",
                {"purge_after": str(purge_after)},
            )
        )
        return Decision(events=tuple(events))
    if command.type == "RestoreRun":
        if state.status is not RunStatus.ARCHIVED:
            raise LifecycleConflict("only an archived Run can be restored")
        return Decision(events=(_event(facts, 0, "RunRestored"),))
    if command.type == "PurgeRun":
        if state.status is not RunStatus.ARCHIVED:
            raise LifecycleConflict("only an archived Run can be purged")
        return Decision(events=(_event(facts, 0, "RunPurged"),))
    return None


class ReducerRegistry:
    def __init__(self, reducers: Mapping[Workflow, WorkflowReducer]) -> None:
        self._reducers = dict(reducers)

    def decide(
        self,
        state: RunState | None,
        command: CommandEnvelope,
        facts: DecisionFacts,
    ) -> Decision:
        reducer = self._reducers.get(command.workflow)
        if reducer is None:
            raise UnknownWorkflow(command.workflow.value)
        if state is not None:
            if state.workflow is not command.workflow:
                raise WorkflowConflict(
                    f"run workflow is {state.workflow.value}, command is {command.workflow.value}"
                )
            if state.owner_scope != command.owner_scope:
                raise OwnerScopeConflict("command owner scope does not match the run")
            if (
                command.expected_stream_version is not None
                and command.expected_stream_version != state.stream_version
            ):
                raise CommandVersionConflict(
                    f"expected {command.expected_stream_version}, current {state.stream_version}"
                )
        elif command.expected_stream_version not in {None, 0}:
            raise CommandVersionConflict(f"expected {command.expected_stream_version}, current 0")
        if state is not None:
            lifecycle = _lifecycle_decision(state, command, facts)
            if lifecycle is not None:
                return lifecycle
        return reducer(state, command, facts)
