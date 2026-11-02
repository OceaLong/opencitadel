"""Immutable external-effect facts within the Run stream, not a second lifecycle."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.execution.commands import ActivityLeaseProof, JsonValue, deep_freeze_json


class EffectCapability(StrEnum):
    NATIVE_SAFE = "native_safe"
    DEDUPE_RESOLVE = "dedupe_resolve"
    DEDUPE_ONLY = "dedupe_only"
    RESOLVE_ONLY = "resolve_only"
    MANUAL_ONLY = "manual_only"


class EffectModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EffectEvidenceRef(EffectModel):
    ref: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EffectOperationSpec(EffectModel):
    operation_id: UUID
    invocation_id: UUID
    run_id: UUID
    activity_id: UUID
    generation: int = Field(ge=0)
    provider_identity: str = Field(min_length=1, max_length=255)
    provider_locator: dict[str, JsonValue] = Field(default_factory=dict)
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=255)
    capability: EffectCapability
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)
    retry_deadline: datetime | None = None
    query_deadline: datetime | None = None
    query_limit: int = Field(default=20, ge=1, le=100)

    @field_validator("provider_locator", "output_schema", mode="after")
    @classmethod
    def _freeze_metadata(cls, value):
        return deep_freeze_json(value)

    @model_validator(mode="after")
    def _bounded_locator(self):
        # Provider locators are registered IDs and operation names, never URLs
        # chosen by an HTTP resolution request or a model's tool arguments.
        if any(
            "url" in key.lower() or "secret" in key.lower() or "token" in key.lower()
            for key in self.provider_locator
        ):
            raise ValueError("effect locator must reference a registered provider")
        for value in (self.retry_deadline, self.query_deadline):
            if value is not None and value.tzinfo is None:
                raise ValueError("effect deadlines require a timezone")
        return self


class EffectObservation(EffectModel):
    receipt_id: UUID
    state: Literal["pending", "unknown", "applied", "not_applied"]
    source: Literal["provider_receipt", "human_verified", "transport_unknown", "kernel_not_sent"]
    evidence: EffectEvidenceRef
    observed_at: datetime
    result_ref: str | None = None
    result_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision_data: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)

    @field_validator("decision_data", mode="after")
    @classmethod
    def _freeze_decision(cls, value):
        return deep_freeze_json(value)

    @model_validator(mode="after")
    def _paired_result(self):
        if (self.result_ref is None) != (self.result_digest is None):
            raise ValueError("result reference requires its digest")
        if self.source == "transport_unknown" and self.state not in {"unknown", "pending"}:
            raise ValueError("transport uncertainty cannot prove an outcome")
        if self.source == "kernel_not_sent" and self.state != "not_applied":
            raise ValueError("unsent authority can only prove not_applied")
        if self.observed_at.tzinfo is None:
            raise ValueError("observation time requires a timezone")
        return self


class EffectState(EffectModel):
    spec: EffectOperationSpec
    state: Literal["prepared", "sending", "pending", "unknown", "applied", "not_applied"] = (
        "prepared"
    )
    revision: int = Field(default=1, ge=1)
    dispatch_proof: ActivityLeaseProof | None = None
    receipt_ids: tuple[UUID, ...] = ()
    latest_receipt: EffectObservation | None = None
    result_ref: str | None = None
    awaiting_result: bool = False
    abandoned: bool = False
    integrity_conflict: bool = False


class PrepareEffectOperation(EffectModel):
    spec: EffectOperationSpec


class EffectRevisionCommand(EffectModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    request_id: UUID | None = None


class StartEffectDispatchCommand(EffectRevisionCommand):
    dispatch_proof: ActivityLeaseProof


class ObserveEffectCommand(EffectRevisionCommand):
    observation: EffectObservation


class AbandonEffectCommand(EffectRevisionCommand):
    reason: str = Field(min_length=1, max_length=2000)


class EffectFact(EffectModel):
    operation_id: UUID
    invocation_id: UUID
    activity_id: UUID
    generation: int = Field(ge=0)
    previous_revision: int = Field(ge=0)
    revision: int = Field(ge=1)
    state: str
    request_id: UUID | None = None
    receipt_id: UUID | None = None
    evidence: EffectEvidenceRef | None = None
    source: str | None = None
    reason: str = Field(default="", max_length=2000)


class EffectPreparedInternal(EffectModel):
    spec: EffectOperationSpec


class EffectDispatchInternal(EffectModel):
    dispatch_proof: ActivityLeaseProof


class EffectReceiptInternal(EffectModel):
    observation: EffectObservation


EFFECT_COMMAND_MODELS = {
    "PrepareEffectOperation": PrepareEffectOperation,
    "StartEffectDispatch": StartEffectDispatchCommand,
    "RecordEffectObservation": ObserveEffectCommand,
    "ResolveActivityOutcome": ObserveEffectCommand,
    "SupplyActivityResult": ObserveEffectCommand,
    "AbandonEffectResolution": AbandonEffectCommand,
}

EFFECT_EVENT_TYPES = frozenset(
    {
        "EffectOperationPrepared",
        "EffectDispatchStarted",
        "EffectReceiptRecorded",
        "EffectOutcomeObserved",
        "EffectManualResolutionAccepted",
        "EffectResultAttached",
        "EffectResolutionAbandoned",
        "EffectIntegrityConflict",
    }
)

TERMINAL_EFFECT_STATES = frozenset({"applied", "not_applied"})


def evolve_effect(operations, event):
    """All operational indexes can be reconstructed from these immutable facts."""
    fact = EffectFact.model_validate(event.public_payload)
    by_id = {item.spec.operation_id: item for item in operations}
    current = by_id.get(fact.operation_id)
    if event.event_type == "EffectOperationPrepared":
        spec = EffectOperationSpec.model_validate(event.internal_payload["spec"])
        if (spec.operation_id, spec.invocation_id, spec.activity_id, spec.generation) != (
            fact.operation_id,
            fact.invocation_id,
            fact.activity_id,
            fact.generation,
        ) or str(spec.run_id) != event.stream_id:
            raise ValueError("effect fact identity mismatch")
        if current is not None and current.spec != spec:
            raise ValueError("effect identity changed during replay")
        by_id[fact.operation_id] = current or EffectState(spec=spec)
    elif current is None:
        raise ValueError("effect fact precedes its identity")
    elif event.event_type in {"EffectOutcomeObserved", "EffectManualResolutionAccepted"}:
        # Audit facets must exactly describe the preceding receipt fact.
        if (
            fact.revision != current.revision
            or EffectObservation.model_validate(event.internal_payload["observation"])
            != current.latest_receipt
        ):
            raise ValueError("effect audit facet differs from its receipt")
    else:
        if fact.previous_revision != current.revision or fact.revision != current.revision + 1:
            raise ValueError("effect revision discontinuity")
        update = {"revision": fact.revision}
        if event.event_type == "EffectDispatchStarted":
            update["state"] = "sending"
            update["dispatch_proof"] = ActivityLeaseProof.model_validate(
                event.internal_payload["dispatch_proof"]
            )
        elif event.event_type == "EffectResolutionAbandoned":
            update["abandoned"] = True
        elif event.event_type == "EffectIntegrityConflict":
            update["integrity_conflict"] = True
            update["receipt_ids"] = (*current.receipt_ids, fact.receipt_id)
        else:
            observation = EffectObservation.model_validate(event.internal_payload["observation"])
            update.update(
                state=observation.state,
                receipt_ids=(*current.receipt_ids, observation.receipt_id),
                latest_receipt=observation,
                result_ref=observation.result_ref or current.result_ref,
                awaiting_result=observation.state == "applied"
                and not (observation.result_ref or current.result_ref),
            )
        by_id[fact.operation_id] = current.model_copy(update=update)
    return tuple(by_id.values())


def decide_effect(aggregate, state, command_name, payload):
    """Pure local authority: adapters may append evidence, never rewrite outcomes."""
    from app.domain.execution.aggregate import Decision
    from app.domain.execution.run import (
        TERMINAL_STATUSES,
        InvalidRunTransitionError,
        RunStatus,
        decision_data_digest,
    )

    def reject(message):
        raise InvalidRunTransitionError(message)

    operations = {item.spec.operation_id: item for item in state.effect_operations}
    if command_name in {"PrepareEffectOperation", "StartEffectDispatch"} and any(
        item.integrity_conflict for item in operations.values()
    ):
        reject("conflicting external evidence requires quarantine")
    if command_name == "PrepareEffectOperation":
        spec = payload.spec
        existing = operations.get(spec.operation_id)
        if existing:
            if existing.spec != spec:
                reject("operation identity has different intent")
            return Decision(events=())
        if state.status in TERMINAL_STATUSES or spec.run_id != state.run_id:
            reject("effect requires its live Run")
        if (
            spec.activity_id not in state.active_activity_ids
            or dict(state.activity_generations).get(spec.activity_id) != spec.generation
        ):
            reject("effect requires its active Activity generation")
        if any(item.spec.invocation_id == spec.invocation_id for item in operations.values()):
            reject("one invocation cannot acquire a second operation")
        fact = EffectFact(
            operation_id=spec.operation_id,
            invocation_id=spec.invocation_id,
            activity_id=spec.activity_id,
            generation=spec.generation,
            previous_revision=0,
            revision=1,
            state="prepared",
        )
        return Decision(
            events=(
                aggregate._new_event(
                    "EffectOperationPrepared",
                    fact.model_dump(mode="json"),
                    {"spec": spec.model_dump(mode="json")},
                ),
            )
        )
    current = operations.get(payload.operation_id)
    if current is None:
        reject("operation is not prepared")
    observation = getattr(payload, "observation", None)
    if observation and observation.receipt_id in current.receipt_ids:
        return Decision(events=())
    if payload.expected_revision != current.revision:
        reject("effect revision changed")
    spec = current.spec
    fact = EffectFact(
        operation_id=spec.operation_id,
        invocation_id=spec.invocation_id,
        activity_id=spec.activity_id,
        generation=spec.generation,
        previous_revision=current.revision,
        revision=current.revision + 1,
        state=current.state,
        request_id=payload.request_id,
    )
    if command_name == "StartEffectDispatch":
        if state.status in TERMINAL_STATUSES or current.state != "prepared" or current.abandoned:
            reject("operation cannot be sent again")
        return Decision(
            events=(
                aggregate._new_event(
                    "EffectDispatchStarted",
                    fact.model_copy(update={"state": "sending"}).model_dump(mode="json"),
                    {"dispatch_proof": payload.dispatch_proof.model_dump(mode="json")},
                ),
            )
        )
    if command_name == "AbandonEffectResolution":
        if current.abandoned:
            return Decision(events=())
        return Decision(
            events=(
                aggregate._new_event(
                    "EffectResolutionAbandoned",
                    fact.model_copy(update={"reason": payload.reason}).model_dump(mode="json"),
                ),
            )
        )
    if current.state == "prepared" and not (
        command_name == "ResolveActivityOutcome"
        and payload.request_id
        and observation is not None
        and observation.source == "kernel_not_sent"
        and observation.state == "not_applied"
    ):
        reject("an unsent operation requires trusted local recovery proof")
    if observation is None:
        reject("receipt required")
    fact = fact.model_copy(
        update={
            "state": observation.state,
            "receipt_id": observation.receipt_id,
            "evidence": observation.evidence,
            "source": observation.source,
            "reason": observation.reason,
        }
    )
    data = {"observation": observation.model_dump(mode="json")}
    if current.state in TERMINAL_EFFECT_STATES and (
        observation.state != current.state
        or (
            current.result_ref is not None
            and observation.result_ref is not None
            and current.result_ref != observation.result_ref
        )
    ):
        events = [
            aggregate._new_event(
                "EffectIntegrityConflict",
                fact.model_copy(update={"state": current.state}).model_dump(mode="json"),
                data,
            )
        ]
        if state.status not in TERMINAL_STATUSES and (
            state.status != RunStatus.WAITING or state.wait_reason != "external_outcome_unknown"
        ):
            events.append(
                aggregate._new_event("RunWaiting", {"reason": "external_outcome_unknown"})
            )
        return Decision(events=tuple(events))
    if command_name == "SupplyActivityResult" and not (
        current.state == "applied"
        and current.awaiting_result
        and observation.state == "applied"
        and observation.result_ref
    ):
        reject("result material only attaches to an applied awaiting-result operation")
    primary = (
        "EffectResultAttached"
        if command_name == "SupplyActivityResult"
        else "EffectReceiptRecorded"
    )
    events = [aggregate._new_event(primary, fact.model_dump(mode="json"), data)]
    if primary == "EffectReceiptRecorded":
        events.append(
            aggregate._new_event(
                "EffectManualResolutionAccepted"
                if observation.source == "human_verified"
                else "EffectOutcomeObserved",
                fact.model_dump(mode="json"),
                data,
            )
        )
    # Late receipts remain audit facts; they do not resurrect a cancelled Run.
    if (
        state.status in TERMINAL_STATUSES
        or command_name == "RecordEffectObservation"
        or current.integrity_conflict
        or current.abandoned
    ):
        return Decision(events=tuple(events))
    settled = next(
        (
            status
            for activity_id, status, generation in state.settled_activities
            if activity_id == spec.activity_id and generation == spec.generation
        ),
        None,
    )
    if settled not in {None, "unknown"}:
        return Decision(events=tuple(events))
    if observation.state == "applied" and observation.result_ref:
        events.append(
            aggregate._new_event(
                "ActivityCompleted",
                {
                    "activity_id": str(spec.activity_id),
                    "generation": spec.generation,
                    "claim_generation": None,
                    "result_ref": observation.result_ref,
                    "result_summary": "External operation result verified",
                    "public_data": {},
                },
                {"decision_digest": decision_data_digest(observation.decision_data)},
            )
        )
    elif observation.state == "not_applied":
        events.append(
            aggregate._new_event(
                "ActivityFailed",
                {
                    "activity_id": str(spec.activity_id),
                    "generation": spec.generation,
                    "claim_generation": None,
                    "failure_code": "EXTERNAL_OPERATION_NOT_APPLIED",
                },
            )
        )
    else:
        reason = (
            "result_material_missing"
            if observation.state == "applied"
            else "external_outcome_unknown"
        )
        if state.status != RunStatus.WAITING or state.wait_reason != reason:
            events.append(aggregate._new_event("RunWaiting", {"reason": reason}))
        return Decision(events=tuple(events))
    other_unknown = any(
        item.spec.operation_id != spec.operation_id
        and (
            item.state in {"sending", "pending", "unknown"}
            or item.awaiting_result
            or item.integrity_conflict
        )
        for item in operations.values()
    )
    if (
        state.status == RunStatus.WAITING
        and state.wait_reason in {"external_outcome_unknown", "result_material_missing"}
        and not other_unknown
    ):
        events.append(aggregate._new_event("RunResumed", {}))
    return Decision(events=tuple(events))
