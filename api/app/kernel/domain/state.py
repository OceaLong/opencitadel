"""Rebuildable Run state and generic event application."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .types import OwnerScopeRef, RunStatus, Workflow


class RunState(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    workflow: Workflow
    owner_scope: OwnerScopeRef
    status: RunStatus
    stream_version: int = Field(ge=0)
    stream_hash: str
    title: str = ""
    current_turn: int = Field(default=0, ge=0)
    wait_reason: str | None = None
    archived_at: datetime | None = None
    purge_after: datetime | None = None
    active_effect_ids: tuple[UUID, ...] = ()
    pending_approvals: tuple[PendingApproval, ...] = ()
    data: dict[str, Any] = Field(default_factory=dict)


class PendingApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    effect_id: UUID
    timer_id: UUID
    reviewer_user_ids: tuple[str, ...]
    expires_at: datetime


def apply_event(state: RunState | None, event) -> RunState:
    """Apply one verified stored event without reading external state."""

    if state is None:
        if event.type != "RunStarted":
            raise ValueError("the first event must be RunStarted")
        state = RunState(
            run_id=event.run_id,
            workflow=Workflow(event.public_payload["workflow"]),
            owner_scope=event.owner_scope,
            status=RunStatus(event.public_payload.get("status", RunStatus.RUNNING.value)),
            stream_version=0,
            stream_hash=event.previous_hash,
            title=str(event.public_payload.get("title", "")),
            data=dict(event.public_payload.get("data") or {}),
        )
    elif state.run_id != event.run_id or state.owner_scope != event.owner_scope:
        raise ValueError("event identity or owner scope changed inside a stream")
    elif event.type == "RunStarted":
        raise ValueError("RunStarted may appear only as the first event")

    update: dict[str, Any] = {
        "stream_version": event.version,
        "stream_hash": event.hash,
    }
    if event.type == "PromptAccepted":
        update.update(status=RunStatus.RUNNING, current_turn=state.current_turn + 1)
    elif event.type == "EffectRequested":
        effect_id = UUID(str(event.public_payload["effect_id"]))
        update["active_effect_ids"] = (*state.active_effect_ids, effect_id)
    elif (
        event.type in {"EffectSucceeded", "EffectFailed", "EffectCancelled"}
        or event.type == "EffectOutcomeUnknown"
    ):
        effect_id = UUID(str(event.public_payload["effect_id"]))
        update["active_effect_ids"] = tuple(
            active for active in state.active_effect_ids if active != effect_id
        )
    elif event.type == "ApprovalRequested":
        update["pending_approvals"] = (
            *state.pending_approvals,
            PendingApproval(
                approval_id=UUID(str(event.public_payload["approval_id"])),
                effect_id=UUID(str(event.public_payload["effect_id"])),
                timer_id=UUID(str(event.public_payload["timer_id"])),
                reviewer_user_ids=tuple(event.public_payload["reviewer_user_ids"]),
                expires_at=event.public_payload["expires_at"],
            ),
        )
    elif event.type in {"ApprovalDecided", "ApprovalExpired", "ApprovalCancelled"}:
        approval_id = event.public_payload.get("approval_id")
        update["pending_approvals"] = (
            ()
            if approval_id is None
            else tuple(
                pending
                for pending in state.pending_approvals
                if pending.approval_id != UUID(str(approval_id))
            )
        )
    elif event.type == "RunWaiting":
        update.update(status=RunStatus.WAITING, wait_reason=event.public_payload.get("reason"))
    elif event.type == "RunResumed":
        update.update(status=RunStatus.RUNNING, wait_reason=None)
    elif event.type == "TurnCompleted":
        update.update(status=RunStatus.IDLE, wait_reason=None)
    elif event.type == "RunCompleted":
        update.update(status=RunStatus.COMPLETED, wait_reason=None)
    elif event.type == "RunFailed":
        update.update(status=RunStatus.FAILED, wait_reason=None)
    elif event.type == "RunCancelled":
        update.update(status=RunStatus.CANCELLED, wait_reason=None)
    elif event.type == "RunArchived":
        raw_purge_after = event.public_payload.get("purge_after")
        update.update(
            status=RunStatus.ARCHIVED,
            wait_reason=None,
            archived_at=event.occurred_at,
            purge_after=(
                datetime.fromisoformat(str(raw_purge_after))
                if raw_purge_after is not None
                else None
            ),
        )
    elif event.type == "RunRestored":
        update.update(
            status=RunStatus.IDLE,
            wait_reason=None,
            archived_at=None,
            purge_after=None,
        )
    elif event.type == "RunPurged":
        update.update(
            status=RunStatus.PURGED,
            wait_reason=None,
            active_effect_ids=(),
            pending_approvals=(),
        )
    elif event.type == "KnowledgeCandidateCreated":
        update["data"] = {**state.data, **event.public_payload}
    elif event.type == "KnowledgeStageCompleted":
        next_stage = event.public_payload.get("next_stage")
        if next_stage is not None:
            update["data"] = {**state.data, "stage": next_stage}
    elif event.type == "KnowledgeVersionPublished":
        update["data"] = {
            **state.data,
            "active_version_id": event.public_payload["version_id"],
        }
    return state.model_copy(update=update)
