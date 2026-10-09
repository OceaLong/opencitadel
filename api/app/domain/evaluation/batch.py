"""Evaluation membership identities and orchestration state, independent of Run execution."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domain.evaluation.configuration import validate_matrix
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.environment import Phase, State

BatchStatus = Literal[
    "created",
    "validating",
    "rejected",
    "queued",
    "running",
    "waiting",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelling",
    "cancelled",
]
TERMINAL_BATCH = frozenset(
    {"rejected", "completed", "completed_with_errors", "failed", "cancelled"}
)
TERMINAL_EXECUTION = frozenset(
    {"succeeded", "failed", "mismatch", "blocked_budget", "blocked", "cancelled", "unknown"}
)
TERMINAL_SCORING = frozenset({"complete", "mismatch", "failed", "skipped", "not_required"})


class CaseSlot(ImmutableModel):
    case_revision_id: UUID
    config_version_id: UUID
    repetition: int = Field(ge=0, le=4)

    def __hash__(self):
        return hash((self.case_revision_id, self.config_version_id, self.repetition))


class CaseResult(ImmutableModel):
    id: UUID
    slot: CaseSlot
    run_id: UUID | None = None
    execution_status: str
    scoring_status: str
    attempt: int = Field(ge=0, le=2)
    result_revision: int = Field(ge=1)


class BatchEnvironment(ImmutableModel):
    """Safe current lease state; retained failures are evidence, not a complete history."""

    id: UUID
    environment_version: UUID
    case_id: UUID
    config_version: UUID
    repeat: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    revision: int = Field(ge=1)
    state: State
    reusable: bool
    prior_failed_operations: dict[Phase, int]


class BatchView(ImmutableModel):
    id: UUID
    revision: int = Field(ge=1)
    status: BatchStatus
    review_status: Literal["not_required", "pending", "complete"] = "not_required"
    cleanup_status: Literal["clean", "pending", "failed"] = "clean"
    counts: dict[str, int]


def admission_key(
    batch_id: str, case_revision: str, config_revision: str, repetition: int, attempt: int
) -> str:
    return "evaluation:" + json.dumps(
        [batch_id, case_revision, config_revision, repetition, attempt], separators=(",", ":")
    )


def schedule_slots(cases, configs, repeat: int, seed: int) -> tuple[CaseSlot, ...]:
    validate_matrix(len(cases), len(configs), repeat)
    if len(set(cases)) != len(cases) or len(set(configs)) != len(configs):
        raise ValueError("duplicate_matrix_member")

    def rank(kind, identity):
        return hashlib.sha256(f"{seed}:{kind}:{identity}".encode()).digest()

    return tuple(
        CaseSlot(case_revision_id=case, config_version_id=config, repetition=repetition)
        for case in sorted(cases, key=lambda identity: rank("case", identity))
        for repetition in range(repeat)
        for config in sorted(configs, key=lambda identity: rank(f"{case}:{repetition}", identity))
    )


def aggregate_status(current, executions, scoring):
    if current in TERMINAL_BATCH:
        return current
    if current == "cancelling":
        return "cancelled" if all(s in TERMINAL_EXECUTION for s in executions) else current
    if not executions:
        return current
    if any(s in {"running", "admitting"} for s in executions) or "running" in scoring:
        return "running"
    if all(s in TERMINAL_EXECUTION for s in executions) and all(
        s in TERMINAL_SCORING for s in scoring
    ):
        return (
            "completed_with_errors"
            if any(s != "succeeded" for s in executions) or "failed" in scoring
            else "completed"
        )
    if any(
        execution == "succeeded" and score == "pending"
        for execution, score in zip(executions, scoring, strict=True)
    ):
        return "running"
    if "queued" in executions:
        return "running" if current in {"running", "waiting"} else "queued"
    return "waiting"


def automatic_retry_allowed(status, attempt, *, infrastructure, unknown):
    return status == "failed" and infrastructure and not unknown and 0 <= attempt < 2


def manual_retry_allowed(execution, scoring, *, resources_available, unknown):
    return (
        resources_available
        and not unknown
        and (
            execution in {"failed", "mismatch", "blocked", "blocked_budget"}
            or scoring in {"mismatch", "failed"}
        )
    )


class ScoringCandidate(ImmutableModel):
    """Accepted successful execution identity for the later scoring consumer."""

    batch_id: UUID
    result_id: UUID
    result_revision: int = Field(ge=1)
    run_id: UUID
    run_revision: int = Field(ge=1)
    suite_version_id: UUID
    case_revision_id: UUID
    config_version_id: UUID


class RecoveryPredecessor(ImmutableModel):
    generation: int
    cleanup: Literal["ready", "pending", "failed"]
