"""Pure production Run aggregate shared by every execution family."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.execution.activity import ActivityRequest
from app.domain.execution.aggregate import Decision
from app.domain.execution.commands import (
    CommandEnvelope,
    JsonValue,
    deep_freeze_json,
)
from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.family import RunFamily
from app.domain.execution.registry import CommandRegistry, EventPayloads, EventRegistry
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.execution.timer import ScheduledCommandRequest
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from app.domain.runtime_policy.snapshot import (
    RunPolicySnapshot,
    validate_run_policy_snapshot,
)


class RunStatus(StrEnum):
    NEW = "new"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})

# Default human-approval time-to-live in minutes: the pure aggregate's fallback
# when the command carries no ttl_minutes. The configurable surface is
# ``OperationsPolicy.approval.ttl_minutes``, injected into RequestApproval
# payloads by the DecisionWorker at submit time.
DEFAULT_APPROVAL_TTL_MINUTES = 1440

# Run-level retry backoff (D6): a retryable failure schedules a durable timer
# that delivers RetryRun after min(base * 2^retry_generation, cap) seconds.
# No jitter on purpose — decide() is a pure, deterministic function (its output
# participates in command idempotency and replay), so the same command must
# always schedule the same timer. Cross-replica thundering-herd concerns do not
# apply here: the timer row is unique per (run_id, retry_generation).
RETRY_BACKOFF_BASE_SECONDS = 5
RETRY_BACKOFF_CAP_SECONDS = 300


class InvalidRunTransitionError(ValueError):
    pass


class ExpectedStreamVersionError(ValueError):
    pass


class UnknownRunCommandError(ValueError):
    pass


def decision_data_digest(decision_data: dict[str, JsonValue]) -> str | None:
    """Digest binding an off-stream decision payload to its settlement event."""
    if not decision_data:
        return None
    return "sha256:" + hashlib.sha256(canonical_json_bytes(decision_data)).hexdigest()


class RunState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    family: RunFamily | None = None
    source_entity_type: str | None = None
    source_entity_id: str | None = None
    semantic_payload: dict[str, JsonValue] = Field(default_factory=dict)
    policy_snapshot: RunPolicySnapshot | None = None
    status: RunStatus = RunStatus.NEW
    active_activity_ids: tuple[UUID, ...] = ()
    started_activity_ids: tuple[UUID, ...] = ()
    activity_generations: tuple[tuple[UUID, int], ...] = ()
    settled_activities: tuple[tuple[UUID, str, int], ...] = ()
    activity_failure_codes: tuple[tuple[UUID, int, str], ...] = ()
    requested_activities: tuple[tuple[UUID, str, int], ...] = ()
    # (activity_id, generation, result_ref, result_summary, decision_digest).
    # The full decision payload lives off-stream on the operational activity
    # task row; the digest here binds it to the hash-chained event history.
    activity_results: tuple[tuple[UUID, int, str | None, str | None, str | None], ...] = ()
    pending_approval_id: UUID | None = None
    pending_approval_activity_id: UUID | None = None
    # (approval_id, decision, feedback). feedback carries the reviewer's note —
    # for clarification approvals it is the option the user picked, which the
    # planner injects back into the approved tool call.
    approval_decisions: tuple[tuple[UUID, str, str], ...] = ()
    retry_generation: int = 0
    wait_reason: str | None = None
    failure_code: str | None = None
    result_ref: str | None = None
    parent_run_id: UUID | None = None
    correlation_id: UUID | None = None
    owner_user_id: str | None = None
    team_id: str | None = None
    stream_version: int = 0
    terminal_event_id: UUID | None = None

    @field_validator("semantic_payload", mode="after")
    @classmethod
    def _freeze_semantic_payload(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EmptyPayload(_Payload):
    pass


class CreateRunPayload(_Payload):
    family: RunFamily
    source_entity_type: Annotated[str, Field(min_length=1, max_length=64)]
    source_entity_id: Annotated[str, Field(min_length=1, max_length=255)]
    parent_run_id: UUID | None = None
    semantic_payload: dict[str, JsonValue]
    public_input: dict[str, JsonValue] = Field(default_factory=dict)
    policy_snapshot: RunPolicySnapshot


class RunCreatedPayload(_Payload):
    family: RunFamily
    source_entity_type: Annotated[str, Field(min_length=1, max_length=64)]
    source_entity_id: Annotated[str, Field(min_length=1, max_length=255)]
    parent_run_id: UUID | None = None
    input: dict[str, JsonValue] = Field(default_factory=dict)


class RunCreatedInternalPayload(_Payload):
    semantic_payload: dict[str, JsonValue] = Field(default_factory=dict)
    policy_snapshot: dict[str, JsonValue]


class WaitRunPayload(_Payload):
    reason: Annotated[str, Field(min_length=1, max_length=128)]


class CompleteRunPayload(_Payload):
    result_ref: Annotated[str, Field(min_length=1)] | None = None


class FailRunPayload(_Payload):
    failure_code: Annotated[str, Field(min_length=1, max_length=128)]
    retryable: bool = False


class RunFailedPayload(_Payload):
    failure_code: Annotated[str, Field(min_length=1, max_length=128)]


class CancelRunPayload(_Payload):
    reason: Annotated[str, Field(min_length=1, max_length=128)] = "requested"


class RequestActivityPayload(_Payload):
    activity_id: UUID
    activity_type: Annotated[str, Field(min_length=1, max_length=128)]
    timeout_at: datetime
    input_ref: Annotated[str, Field(min_length=1)] | None = None
    input_digest: Annotated[str, Field(min_length=1, max_length=128)]
    input_payload: dict[str, JsonValue] = Field(default_factory=dict)
    public_data: dict[str, JsonValue] = Field(default_factory=dict)


class ActivityRequestedInternalPayload(_Payload):
    input_payload: dict[str, JsonValue] = Field(default_factory=dict)


class ActivityResultPayload(_Payload):
    activity_id: UUID
    generation: int = Field(ge=0)
    result_ref: Annotated[str, Field(min_length=1)] | None = None
    result_summary: Annotated[str, Field(max_length=4096)] | None = None
    decision_data: dict[str, JsonValue] = Field(default_factory=dict)
    public_data: dict[str, JsonValue] = Field(default_factory=dict)


class ActivityCompletedInternalPayload(_Payload):
    decision_digest: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class ActivityFailurePayload(_Payload):
    activity_id: UUID
    generation: int = Field(ge=0)
    failure_code: Annotated[str, Field(min_length=1, max_length=128)]


class RequestApprovalPayload(_Payload):
    approval_id: UUID
    subject_activity_id: UUID
    approval_kind: Annotated[str, Field(min_length=1, max_length=64)]
    risk_summary: Annotated[str, Field(min_length=1, max_length=1024)]
    subject_label: Annotated[str, Field(min_length=1, max_length=128)]
    # Bounds mirror OperationsPolicy.approval.ttl_minutes (1 minute .. 30 days).
    ttl_minutes: Annotated[int, Field(ge=1, le=43_200)] | None = None
    # Clarification approvals: selectable options the reviewer picks from
    # (rendered as the clarification card); the pick returns via
    # DecideApproval.feedback. Greenfield v1 extension (pre-release window).
    choices: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] | None = Field(
        default=None, max_length=6
    )


class DecideApprovalPayload(_Payload):
    approval_id: UUID
    decision: Literal["approved", "rejected"]
    actor_user_id: Annotated[str, Field(min_length=1, max_length=255)]
    feedback: Annotated[str, Field(max_length=2048)] = ""


class ExpireApprovalPayload(_Payload):
    approval_id: UUID


_COMMAND_PAYLOADS: dict[str, type[BaseModel]] = {
    "CreateRun": CreateRunPayload,
    "StartRun": EmptyPayload,
    "WaitRun": WaitRunPayload,
    "ResumeRun": EmptyPayload,
    "RetryRun": EmptyPayload,
    "CompleteRun": CompleteRunPayload,
    "FailRun": FailRunPayload,
    "CancelRun": CancelRunPayload,
    "RequestActivity": RequestActivityPayload,
    "MarkActivityCallStarted": ActivityResultPayload,
    "CompleteActivity": ActivityResultPayload,
    "FailActivity": ActivityFailurePayload,
    "MarkActivityOutcomeUnknown": ActivityFailurePayload,
    "RequestApproval": RequestApprovalPayload,
    "DecideApproval": DecideApprovalPayload,
    "ExpireApproval": ExpireApprovalPayload,
}

# name -> (public payload model, internal payload model). The internal model is
# mandatory: events with no internal data declare EmptyPayload, so accidental
# internal leakage fails at emit time instead of surfacing on a later replay.
_EVENT_SPECS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "RunCreated": (RunCreatedPayload, RunCreatedInternalPayload),
    "RunStarted": (EmptyPayload, EmptyPayload),
    "RunWaiting": (WaitRunPayload, EmptyPayload),
    "RunResumed": (EmptyPayload, EmptyPayload),
    "RunRetried": (EmptyPayload, EmptyPayload),
    "RunCompleted": (CompleteRunPayload, EmptyPayload),
    "RunFailed": (RunFailedPayload, EmptyPayload),
    "RunCancelled": (CancelRunPayload, EmptyPayload),
    "RunAttemptFailed": (FailRunPayload, EmptyPayload),
    "ActivityRequested": (RequestActivityPayload, ActivityRequestedInternalPayload),
    "ActivityCallStarted": (ActivityResultPayload, EmptyPayload),
    "ActivityCompleted": (ActivityResultPayload, ActivityCompletedInternalPayload),
    "ActivityFailed": (ActivityFailurePayload, EmptyPayload),
    "ActivityOutcomeUnknown": (ActivityFailurePayload, EmptyPayload),
    "ActivityCancelled": (ActivityFailurePayload, EmptyPayload),
    "ApprovalRequested": (RequestApprovalPayload, EmptyPayload),
    "ApprovalDecided": (DecideApprovalPayload, EmptyPayload),
    "ApprovalExpired": (ExpireApprovalPayload, EmptyPayload),
}

# Every event type evolve() handles, maintained by hand next to the if-chain.
# The registry self-check in __init__ cross-references this set against
# _EVENT_SPECS, so registering an event without teaching evolve() about it (or
# vice versa) fails at aggregate construction instead of on a later replay.
_EVOLVED_EVENT_TYPES = frozenset(
    {
        "RunCreated",
        "RunStarted",
        "RunWaiting",
        "RunResumed",
        "RunRetried",
        "RunCompleted",
        "RunFailed",
        "RunCancelled",
        "RunAttemptFailed",
        "ActivityRequested",
        "ActivityCallStarted",
        "ActivityCompleted",
        "ActivityFailed",
        "ActivityOutcomeUnknown",
        "ActivityCancelled",
        "ApprovalRequested",
        "ApprovalDecided",
        "ApprovalExpired",
    }
)


class RunAggregate:
    """One deterministic state machine for all execution lifecycles."""

    state_type = RunState
    snapshot_serializer_version = 5

    def __init__(self) -> None:
        self.command_registry = CommandRegistry()
        for name, payload_type in _COMMAND_PAYLOADS.items():
            self.command_registry.register(name, 1, payload_type)
        self.event_registry = EventRegistry()
        for name, (public_model, internal_model) in _EVENT_SPECS.items():
            self.event_registry.register(
                name,
                1,
                public_model,
                internal_model=internal_model,
            )
        self._assert_registry_coverage()

    def _assert_registry_coverage(self) -> None:
        """Fail at construction when a wiring seam is missing, not on replay."""
        registered_events = self.event_registry.registered_names()
        if registered_events != _EVOLVED_EVENT_TYPES:
            raise RuntimeError(
                "event registry and evolve() coverage diverged: "
                f"{sorted(registered_events ^ _EVOLVED_EVENT_TYPES)}"
            )
        for name in self.command_registry.registered_names():
            if getattr(self, f"_decide_{name}", None) is None:
                raise RuntimeError(f"registered command {name} has no _decide_{name} handler")

    def initial_state(self, stream_id: str) -> RunState:
        return RunState(run_id=UUID(stream_id))

    def evolve(self, state: RunState, event: StoredEvent) -> RunState:
        self._validate_event(state, event)
        payload = event.public_payload
        common = {"stream_version": event.stream_version}

        if event.event_type == "RunCreated":
            raw_snapshot = event.internal_payload.get("policy_snapshot")
            try:
                policy_snapshot = RunPolicySnapshot.model_validate(raw_snapshot)
            except ValidationError as exc:
                raise RuntimePolicyIntegrityError(
                    "RunCreated policy snapshot is missing or invalid"
                ) from exc
            validate_run_policy_snapshot(policy_snapshot)
            if policy_snapshot.family.value != payload["family"]:
                raise RuntimePolicyIntegrityError(
                    "RunCreated policy snapshot family does not match public metadata"
                )
            return state.model_copy(
                update={
                    **common,
                    "family": RunFamily(payload["family"]),
                    "source_entity_type": payload["source_entity_type"],
                    "source_entity_id": payload["source_entity_id"],
                    "semantic_payload": event.internal_payload.get("semantic_payload", {}),
                    "policy_snapshot": policy_snapshot,
                    "parent_run_id": self._optional_uuid(payload["parent_run_id"]),
                    "correlation_id": event.correlation_id,
                    "owner_user_id": event.owner_user_id,
                    "team_id": event.team_id,
                    "status": RunStatus.QUEUED,
                }
            )
        if event.event_type == "RunStarted":
            return state.model_copy(
                update={**common, "status": RunStatus.RUNNING, "wait_reason": None}
            )
        if event.event_type == "RunWaiting":
            return state.model_copy(
                update={
                    **common,
                    "status": RunStatus.WAITING,
                    "wait_reason": payload["reason"],
                }
            )
        if event.event_type == "RunResumed":
            return state.model_copy(
                update={**common, "status": RunStatus.RUNNING, "wait_reason": None}
            )
        if event.event_type == "RunAttemptFailed":
            return state.model_copy(
                update={
                    **common,
                    "status": RunStatus.WAITING,
                    "wait_reason": "retry",
                    "failure_code": payload["failure_code"],
                }
            )
        if event.event_type == "RunRetried":
            return state.model_copy(
                update={
                    **common,
                    "status": RunStatus.QUEUED,
                    "retry_generation": state.retry_generation + 1,
                    "wait_reason": None,
                    "failure_code": None,
                }
            )
        if event.event_type == "ApprovalRequested":
            return state.model_copy(
                update={
                    **common,
                    "status": RunStatus.WAITING,
                    "wait_reason": "approval",
                    "pending_approval_id": UUID(str(payload["approval_id"])),
                    "pending_approval_activity_id": UUID(str(payload["subject_activity_id"])),
                }
            )
        if event.event_type == "ApprovalDecided":
            approval_id = UUID(str(payload["approval_id"]))
            decisions = {
                decided_id: (decision, feedback)
                for decided_id, decision, feedback in state.approval_decisions
            }
            decisions[approval_id] = (
                str(payload["decision"]),
                str(payload.get("feedback") or ""),
            )
            return state.model_copy(
                update={
                    **common,
                    "pending_approval_id": None,
                    "pending_approval_activity_id": None,
                    "approval_decisions": tuple(
                        sorted(
                            (
                                (decided_id, decision, feedback)
                                for decided_id, (decision, feedback) in decisions.items()
                            ),
                            key=lambda item: str(item[0]),
                        )
                    ),
                }
            )
        if event.event_type == "ApprovalExpired":
            approval_id = UUID(str(payload["approval_id"]))
            decisions = {
                decided_id: (decision, feedback)
                for decided_id, decision, feedback in state.approval_decisions
            }
            decisions[approval_id] = ("expired", "")
            return state.model_copy(
                update={
                    **common,
                    "pending_approval_id": None,
                    "pending_approval_activity_id": None,
                    "approval_decisions": tuple(
                        sorted(
                            (
                                (decided_id, decision, feedback)
                                for decided_id, (decision, feedback) in decisions.items()
                            ),
                            key=lambda item: str(item[0]),
                        )
                    ),
                }
            )
        if event.event_type in {"RunCompleted", "RunFailed", "RunCancelled"}:
            status = {
                "RunCompleted": RunStatus.COMPLETED,
                "RunFailed": RunStatus.FAILED,
                "RunCancelled": RunStatus.CANCELLED,
            }[event.event_type]
            return state.model_copy(
                update={
                    **common,
                    "status": status,
                    "active_activity_ids": (),
                    "started_activity_ids": (),
                    "terminal_event_id": event.event_id,
                    "result_ref": payload.get("result_ref"),
                    "failure_code": payload.get("failure_code"),
                    "wait_reason": None,
                    "pending_approval_id": None,
                    "pending_approval_activity_id": None,
                }
            )
        if event.event_type == "ActivityRequested":
            activity_id = UUID(str(payload["activity_id"]))
            generations = dict(state.activity_generations)
            generations[activity_id] = state.retry_generation
            return state.model_copy(
                update={
                    **common,
                    "active_activity_ids": tuple(
                        sorted((*state.active_activity_ids, activity_id), key=str)
                    ),
                    "activity_generations": tuple(
                        sorted(generations.items(), key=lambda item: str(item[0]))
                    ),
                    "requested_activities": tuple(
                        sorted(
                            (
                                *state.requested_activities,
                                (
                                    activity_id,
                                    str(payload["activity_type"]),
                                    state.retry_generation,
                                ),
                            ),
                            key=lambda item: str(item[0]),
                        )
                    ),
                }
            )
        if event.event_type == "ActivityCallStarted":
            activity_id = UUID(str(payload["activity_id"]))
            return state.model_copy(
                update={
                    **common,
                    "started_activity_ids": tuple(
                        sorted((*state.started_activity_ids, activity_id), key=str)
                    ),
                }
            )
        if event.event_type in {
            "ActivityCompleted",
            "ActivityFailed",
            "ActivityOutcomeUnknown",
            "ActivityCancelled",
        }:
            activity_id = UUID(str(payload["activity_id"]))
            generation = int(payload["generation"])
            status = {
                "ActivityCompleted": "succeeded",
                "ActivityFailed": "failed",
                "ActivityOutcomeUnknown": "unknown",
                "ActivityCancelled": "cancelled",
            }[event.event_type]
            activity_results = state.activity_results
            if event.event_type == "ActivityCompleted":
                decision_digest = event.internal_payload.get("decision_digest")
                if decision_digest is not None and not isinstance(decision_digest, str):
                    raise ValueError("Activity decision_digest must be a string")
                # Result references are rehydrated as provider conversation history.
                # Preserve their causal event order; UUID order is deterministic but
                # semantically meaningless and can place a tool result before the
                # assistant tool call that produced it.
                activity_results = (
                    *state.activity_results,
                    (
                        activity_id,
                        generation,
                        payload.get("result_ref"),
                        payload.get("result_summary"),
                        decision_digest,
                    ),
                )
            activity_failure_codes = state.activity_failure_codes
            if event.event_type in {
                "ActivityFailed",
                "ActivityOutcomeUnknown",
                "ActivityCancelled",
            }:
                failure_code = payload.get("failure_code")
                if not isinstance(failure_code, str) or not failure_code:
                    raise ValueError("failed Activity requires a failure code")
                activity_failure_codes = tuple(
                    sorted(
                        (
                            *state.activity_failure_codes,
                            (activity_id, generation, failure_code),
                        ),
                        key=lambda item: (str(item[0]), item[1]),
                    )
                )
            return state.model_copy(
                update={
                    **common,
                    "active_activity_ids": tuple(
                        item for item in state.active_activity_ids if item != activity_id
                    ),
                    "started_activity_ids": tuple(
                        item for item in state.started_activity_ids if item != activity_id
                    ),
                    "settled_activities": tuple(
                        sorted(
                            (
                                *state.settled_activities,
                                (activity_id, status, generation),
                            ),
                            key=lambda item: str(item[0]),
                        )
                    ),
                    "activity_results": activity_results,
                    "activity_failure_codes": activity_failure_codes,
                }
            )
        raise ValueError(f"unknown Run event: {event.event_type}")

    def decide(self, state: RunState, command: CommandEnvelope) -> Decision:
        self._validate_target(state, command)
        if command.command_type not in _COMMAND_PAYLOADS:
            raise UnknownRunCommandError(command.command_type)
        expected_schema_version = self.command_registry.latest_version(command.command_type)
        if command.command_schema_version != expected_schema_version:
            raise ValueError(
                f"unsupported {command.command_type} schema version: "
                f"{command.command_schema_version}"
            )
        if (
            command.expected_stream_version is not None
            and command.expected_stream_version != state.stream_version
        ):
            raise ExpectedStreamVersionError(
                "expected stream version "
                f"{command.expected_stream_version}, current {state.stream_version}"
            )

        payload = _COMMAND_PAYLOADS[command.command_type].model_validate(command.payload)
        if state.status in TERMINAL_STATUSES:
            matching = {
                RunStatus.COMPLETED: "CompleteRun",
                RunStatus.FAILED: "FailRun",
                RunStatus.CANCELLED: "CancelRun",
            }[state.status]
            if command.command_type == matching:
                return Decision(events=())
            # A durable approval-timeout timer may still fire after the Run has
            # already terminated (its cancellation raced the timer claim). The
            # expiry is moot, so absorb it idempotently instead of dead-lettering.
            if command.command_type == "ExpireApproval":
                return Decision(events=())
            raise InvalidRunTransitionError(
                f"cannot handle {command.command_type} from terminal {state.status}"
            )

        # RequestApproval and FailRun schedule durable timers whose due times
        # are derived from the issuing command's clock; the handlers need the
        # envelope's issued_at, so they are dispatched explicitly rather than
        # by name.
        if command.command_type == "RequestApproval":
            return self._decide_RequestApproval(state, payload, issued_at=command.issued_at)
        if command.command_type == "FailRun":
            return self._decide_FailRun(state, payload, issued_at=command.issued_at)

        handler = getattr(self, f"_decide_{command.command_type}", None)
        if handler is None:
            raise UnknownRunCommandError(command.command_type)
        return handler(state, payload)

    def _decide_CreateRun(self, state: RunState, payload: CreateRunPayload) -> Decision:
        if state.status != RunStatus.NEW:
            raise InvalidRunTransitionError("Run can only be created once")
        policy_snapshot = validate_run_policy_snapshot(payload.policy_snapshot)
        if policy_snapshot.family is not payload.family:
            raise RuntimePolicyIntegrityError(
                "CreateRun policy snapshot family does not match Run family"
            )
        return Decision(
            events=(
                self._new_event(
                    "RunCreated",
                    RunCreatedPayload(
                        family=payload.family,
                        source_entity_type=payload.source_entity_type,
                        source_entity_id=payload.source_entity_id,
                        parent_run_id=payload.parent_run_id,
                        input=payload.public_input,
                    ).model_dump(mode="json"),
                    internal_payload={
                        "semantic_payload": payload.semantic_payload,
                        "policy_snapshot": policy_snapshot.model_dump(mode="json"),
                    },
                ),
            )
        )

    def _decide_StartRun(self, state: RunState, payload: EmptyPayload) -> Decision:
        del payload
        if state.status != RunStatus.QUEUED:
            raise InvalidRunTransitionError("Run can only start from queued")
        return self._event("RunStarted", {})

    def _decide_WaitRun(self, state: RunState, payload: WaitRunPayload) -> Decision:
        if state.status != RunStatus.RUNNING:
            raise InvalidRunTransitionError("Run can only wait from running")
        return self._event("RunWaiting", payload.model_dump(mode="json"))

    def _decide_ResumeRun(self, state: RunState, payload: EmptyPayload) -> Decision:
        del payload
        if state.status != RunStatus.WAITING or state.wait_reason == "retry":
            raise InvalidRunTransitionError("Run is not resumable")
        return self._event("RunResumed", {})

    def _decide_RetryRun(self, state: RunState, payload: EmptyPayload) -> Decision:
        del payload
        if state.status != RunStatus.WAITING or state.wait_reason != "retry":
            raise InvalidRunTransitionError("Run has no retryable failure")
        if state.active_activity_ids:
            raise InvalidRunTransitionError("active Activities must settle before retry")
        return self._event("RunRetried", {})

    def _decide_RequestApproval(
        self,
        state: RunState,
        payload: RequestApprovalPayload,
        *,
        issued_at: datetime,
    ) -> Decision:
        if state.status != RunStatus.RUNNING or state.active_activity_ids:
            raise InvalidRunTransitionError("approval requires an idle running Run")
        prior = next(
            (
                decision
                for decided_id, decision, _ in state.approval_decisions
                if decided_id == payload.approval_id
            ),
            None,
        )
        if prior is not None:
            raise InvalidRunTransitionError("approval identity was already decided")
        # Mirror the Activity-request timeout: a durable, self-cancelling command
        # fences the WAITING state so an unanswered approval cannot pin the Run
        # forever. The timer cancels itself the moment the approval is decided or
        # the Run reaches any terminal state.
        due_at = issued_at + timedelta(minutes=payload.ttl_minutes or DEFAULT_APPROVAL_TTL_MINUTES)
        timer_id = uuid5(
            NAMESPACE_URL,
            f"opencitadel:approval-timeout:{payload.approval_id}",
        )
        timeout = ScheduledCommandRequest(
            timer_id=timer_id,
            due_at=due_at,
            command=CommandEnvelope(
                command_id=timer_id,
                command_type="ExpireApproval",
                command_schema_version=1,
                stream_type="run",
                stream_id=str(state.run_id),
                expected_stream_version=None,
                owner_user_id=state.owner_user_id,
                team_id=state.team_id,
                correlation_id=state.correlation_id or payload.approval_id,
                causation_id=payload.approval_id,
                issued_at=due_at,
                payload={"approval_id": str(payload.approval_id)},
            ),
            cancellation_event_types=frozenset(
                {
                    "ApprovalDecided",
                    "RunCompleted",
                    "RunFailed",
                    "RunCancelled",
                }
            ),
            cancellation_activity_id=None,
        )
        return Decision(
            events=(
                self._new_event(
                    "ApprovalRequested",
                    payload.model_dump(mode="json"),
                ),
            ),
            scheduled_commands=(timeout,),
        )

    def _decide_ExpireApproval(
        self,
        state: RunState,
        payload: ExpireApprovalPayload,
    ) -> Decision:
        # Idempotent: if the approval was decided (or the Run otherwise moved on)
        # before the timer fired, the expiry is a no-op. Only an approval that is
        # still the pending one is expired.
        if (
            state.status != RunStatus.WAITING
            or state.wait_reason != "approval"
            or state.pending_approval_id != payload.approval_id
        ):
            return Decision(events=())
        # TODO(E3): escalation/reassignment. For now an expired approval follows
        # "rejected" semantics -- it advances the Run to a terminal CANCELLED
        # state so it is no longer stuck WAITING.
        expired = self._new_event("ApprovalExpired", payload.model_dump(mode="json"))
        cancelled = self._new_event("RunCancelled", {"reason": "approval_expired"})
        return Decision(events=(expired, cancelled))

    def _decide_DecideApproval(
        self,
        state: RunState,
        payload: DecideApprovalPayload,
    ) -> Decision:
        if (
            state.status != RunStatus.WAITING
            or state.wait_reason != "approval"
            or state.pending_approval_id != payload.approval_id
        ):
            raise InvalidRunTransitionError("approval is not pending")
        decided = self._new_event("ApprovalDecided", payload.model_dump(mode="json"))
        if payload.decision == "approved":
            continuation = self._new_event("RunResumed", {})
        else:
            continuation = self._new_event("RunCancelled", {"reason": "approval_rejected"})
        return Decision(events=(decided, continuation))

    def _decide_CompleteRun(self, state: RunState, payload: CompleteRunPayload) -> Decision:
        if state.status != RunStatus.RUNNING or state.active_activity_ids:
            raise InvalidRunTransitionError("Run is not ready to complete")
        return self._event("RunCompleted", payload.model_dump(mode="json"))

    def _decide_FailRun(
        self,
        state: RunState,
        payload: FailRunPayload,
        *,
        issued_at: datetime,
    ) -> Decision:
        if state.status not in {RunStatus.RUNNING, RunStatus.WAITING}:
            raise InvalidRunTransitionError("Run cannot fail from its current state")
        if payload.retryable:
            # D6: the retry is timer-driven instead of decision-loop-driven. A
            # durable, deterministic timer delivers RetryRun after exponential
            # backoff; the decision loop treats WAITING(retry) as idle (see
            # decisions/base.py lifecycle_command), which prevents the old
            # zero-delay retry storm. The timer cancels itself if the Run is
            # retried (this timer fired), cancelled, or otherwise terminated
            # before it fires.
            delay_seconds = min(
                RETRY_BACKOFF_BASE_SECONDS * (2**state.retry_generation),
                RETRY_BACKOFF_CAP_SECONDS,
            )
            due_at = issued_at + timedelta(seconds=delay_seconds)
            timer_id = uuid5(
                NAMESPACE_URL,
                f"opencitadel:run-retry:{state.run_id}:{state.retry_generation}",
            )
            retry = ScheduledCommandRequest(
                timer_id=timer_id,
                due_at=due_at,
                command=CommandEnvelope(
                    command_id=timer_id,
                    command_type="RetryRun",
                    command_schema_version=1,
                    stream_type="run",
                    stream_id=str(state.run_id),
                    expected_stream_version=None,
                    owner_user_id=state.owner_user_id,
                    team_id=state.team_id,
                    correlation_id=state.correlation_id or state.run_id,
                    causation_id=None,
                    issued_at=due_at,
                    payload={},
                ),
                cancellation_event_types=frozenset(
                    {
                        "RunRetried",
                        "RunCancelled",
                        "RunCompleted",
                        "RunFailed",
                    }
                ),
                cancellation_activity_id=None,
            )
            return Decision(
                events=(
                    self._new_event(
                        "RunAttemptFailed",
                        payload.model_dump(mode="json"),
                    ),
                ),
                scheduled_commands=(retry,),
            )
        event_payload: dict[str, JsonValue] = {
            "failure_code": payload.failure_code,
        }
        return self._event("RunFailed", event_payload)

    def _decide_CancelRun(self, state: RunState, payload: CancelRunPayload) -> Decision:
        if state.status == RunStatus.NEW:
            raise InvalidRunTransitionError("uncreated Run cannot be cancelled")
        generations = dict(state.activity_generations)
        cancelled_activities = tuple(
            self._new_event(
                "ActivityCancelled",
                {
                    "activity_id": str(activity_id),
                    "generation": generations[activity_id],
                    "failure_code": "ACTIVITY_CANCELLED",
                },
            )
            for activity_id in state.active_activity_ids
        )
        return Decision(
            events=(
                *cancelled_activities,
                self._new_event("RunCancelled", payload.model_dump(mode="json")),
            )
        )

    def _decide_RequestActivity(
        self,
        state: RunState,
        payload: RequestActivityPayload,
    ) -> Decision:
        if state.status != RunStatus.RUNNING:
            raise InvalidRunTransitionError("Activities require a running Run")
        if payload.activity_id in state.active_activity_ids:
            return Decision(events=())
        if any(
            activity_id == payload.activity_id for activity_id, _, _ in state.settled_activities
        ):
            raise InvalidRunTransitionError("Activity identity was already settled")
        request = ActivityRequest(
            activity_id=payload.activity_id,
            activity_type=payload.activity_type,
            aggregate_type="run",
            aggregate_id=str(state.run_id),
            generation=state.retry_generation,
            timeout_at=payload.timeout_at,
            input_ref=payload.input_ref,
            input_digest=payload.input_digest,
            input_payload=payload.input_payload,
        )
        timer_id = uuid5(
            NAMESPACE_URL,
            f"opencitadel:activity-timeout:{payload.activity_id}:{state.retry_generation}",
        )
        timeout = ScheduledCommandRequest(
            timer_id=timer_id,
            due_at=payload.timeout_at,
            command=CommandEnvelope(
                command_id=timer_id,
                command_type="FailActivity",
                command_schema_version=1,
                stream_type="run",
                stream_id=str(state.run_id),
                expected_stream_version=None,
                owner_user_id=state.owner_user_id,
                team_id=state.team_id,
                correlation_id=state.correlation_id or payload.activity_id,
                causation_id=payload.activity_id,
                issued_at=payload.timeout_at,
                payload={
                    "activity_id": str(payload.activity_id),
                    "generation": state.retry_generation,
                    "failure_code": "ACTIVITY_TIMEOUT",
                },
            ),
            cancellation_event_types=frozenset(
                {
                    "ActivityCompleted",
                    "ActivityFailed",
                    "ActivityOutcomeUnknown",
                    "ActivityCancelled",
                    "RunCompleted",
                    "RunFailed",
                    "RunCancelled",
                }
            ),
            cancellation_activity_id=payload.activity_id,
        )
        return Decision(
            events=(
                self._new_event(
                    "ActivityRequested",
                    payload.model_dump(
                        mode="json",
                        exclude={"input_payload"},
                    ),
                    internal_payload={"input_payload": payload.input_payload},
                ),
            ),
            activity_requests=(request,),
            scheduled_commands=(timeout,),
        )

    def _decide_MarkActivityCallStarted(
        self,
        state: RunState,
        payload: ActivityResultPayload,
    ) -> Decision:
        RunAggregate._validate_active_generation(state, payload)
        if payload.activity_id in state.started_activity_ids:
            return Decision(events=())
        return self._event("ActivityCallStarted", payload.model_dump(mode="json"))

    def _decide_CompleteActivity(
        self,
        state: RunState,
        payload: ActivityResultPayload,
    ) -> Decision:
        if RunAggregate._is_duplicate_settlement(state, payload, expected_status="succeeded"):
            return Decision(events=())
        RunAggregate._validate_active_generation(state, payload)
        # The decision payload itself stays off-stream (on the operational
        # activity task row, written in the same transaction); the event only
        # carries a digest that binds that row to the hash-chained history.
        return Decision(
            events=(
                self._new_event(
                    "ActivityCompleted",
                    payload.model_dump(
                        mode="json",
                        exclude={"decision_data"},
                    ),
                    internal_payload={
                        "decision_digest": decision_data_digest(payload.decision_data),
                    },
                ),
            )
        )

    def _decide_FailActivity(
        self,
        state: RunState,
        payload: ActivityFailurePayload,
    ) -> Decision:
        if RunAggregate._is_duplicate_settlement(state, payload, expected_status="failed"):
            return Decision(events=())
        RunAggregate._validate_active_generation(state, payload)
        return self._event("ActivityFailed", payload.model_dump(mode="json"))

    def _decide_MarkActivityOutcomeUnknown(
        self,
        state: RunState,
        payload: ActivityFailurePayload,
    ) -> Decision:
        if RunAggregate._is_duplicate_settlement(state, payload, expected_status="unknown"):
            return Decision(events=())
        RunAggregate._validate_active_generation(state, payload)
        return self._event("ActivityOutcomeUnknown", payload.model_dump(mode="json"))

    @staticmethod
    def _validate_active_generation(
        state: RunState,
        payload: ActivityResultPayload | ActivityFailurePayload,
    ) -> None:
        if payload.activity_id not in state.active_activity_ids:
            raise InvalidRunTransitionError("Activity is not active")
        expected = dict(state.activity_generations)[payload.activity_id]
        if payload.generation != expected:
            raise InvalidRunTransitionError("stale Activity generation")

    @staticmethod
    def _is_duplicate_settlement(
        state: RunState,
        payload: ActivityResultPayload | ActivityFailurePayload,
        *,
        expected_status: str,
    ) -> bool:
        settled = {
            activity_id: (status, generation)
            for activity_id, status, generation in state.settled_activities
        }.get(payload.activity_id)
        if settled is None:
            return False
        if settled == (expected_status, payload.generation):
            return True
        raise InvalidRunTransitionError("Activity has a different settled outcome")

    def _new_event(
        self,
        event_type: str,
        public_payload: dict[str, JsonValue],
        internal_payload: dict[str, JsonValue] | None = None,
    ) -> NewEvent:
        """Emit one event, write-side validated against the registry.

        A typo'd event type or a payload that no longer matches the registered
        model fails here — before anything is persisted — instead of surfacing
        as an unreplayable stream later.
        """
        version = self.event_registry.latest_version(event_type)
        internal = internal_payload or {}
        self.event_registry.validate_new(
            event_type,
            version,
            EventPayloads(public=dict(public_payload), internal=dict(internal)),
        )
        return NewEvent(
            event_type=event_type,
            event_schema_version=version,
            public_payload=public_payload,
            internal_payload=internal,
        )

    def _event(self, event_type: str, payload: dict[str, JsonValue]) -> Decision:
        return Decision(events=(self._new_event(event_type, payload),))

    @staticmethod
    def _validate_target(state: RunState, command: CommandEnvelope) -> None:
        if command.stream_type != "run":
            raise ValueError("command is not addressed to a Run")
        if command.stream_id != str(state.run_id):
            raise ValueError("command belongs to a different Run")

    def _validate_event(self, state: RunState, event: StoredEvent) -> None:
        if event.stream_type != "run" or event.stream_id != str(state.run_id):
            raise ValueError("event belongs to a different Run")
        if event.stream_version != state.stream_version + 1:
            raise ValueError("Run event versions must be contiguous")
        expected_schema_version = self.event_registry.latest_version(event.event_type)
        if event.event_schema_version != expected_schema_version:
            raise ValueError(
                f"unsupported {event.event_type} schema version: {event.event_schema_version}"
            )
        if event.event_type != "RunCreated" and state.status == RunStatus.NEW:
            raise InvalidRunTransitionError("RunCreated must be the first event")
        if state.status in TERMINAL_STATUSES:
            raise InvalidRunTransitionError("terminal Run cannot evolve")

    @staticmethod
    def _optional_uuid(value: JsonValue) -> UUID | None:
        return None if value is None else UUID(str(value))


def validated_run_policy_snapshot(state: RunState) -> RunPolicySnapshot:
    """Return the cryptographically valid snapshot of an admitted Run."""
    if state.family is None or state.policy_snapshot is None:
        raise RuntimePolicyIntegrityError("created Run is missing its policy snapshot")
    snapshot = validate_run_policy_snapshot(state.policy_snapshot)
    if snapshot.family != state.family:
        raise RuntimePolicyIntegrityError("Run state policy snapshot family mismatch")
    return snapshot


__all__ = [
    "TERMINAL_STATUSES",
    "ExpectedStreamVersionError",
    "InvalidRunTransitionError",
    "RunAggregate",
    "RunFamily",
    "RunState",
    "RunStatus",
    "UnknownRunCommandError",
    "decision_data_digest",
    "validated_run_policy_snapshot",
]
