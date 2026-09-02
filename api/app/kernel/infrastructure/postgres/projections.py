"""Small transactional projection handlers for kernel events."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.knowledge.models import KnowledgeBaseORM, KnowledgeVersionORM
from app.kernel.domain.events import StoredEvent
from app.kernel.domain.types import RunStatus

from .models import (
    KernelApprovalReviewerORM,
    KernelApprovalViewORM,
    KernelEffectViewORM,
    KernelMessageViewORM,
    KernelNotificationViewORM,
    KernelPublicEventORM,
    KernelResourceBuildViewORM,
    KernelRunViewORM,
)


def _scope_values(event: StoredEvent) -> dict[str, str | None]:
    return {
        "owner_user_id": event.owner_scope.owner_user_id,
        "team_id": event.owner_scope.team_id,
    }


class ProjectionRegistry:
    """Apply allow-listed views inside the event-append transaction."""

    async def apply(
        self,
        session: AsyncSession,
        event: StoredEvent,
        private_payload: dict[str, object],
    ) -> None:
        await self._run(session, event)
        await self._messages(session, event, private_payload)
        await self._effects(session, event)
        await self._approvals(session, event)
        await self._resource_build(session, event)
        await self._knowledge_publication(session, event)
        session.add(
            KernelPublicEventORM(
                event_id=event.event_id,
                run_id=event.run_id,
                event_version=event.version,
                event_type=event.type,
                payload=event.public_payload,
                occurred_at=event.occurred_at,
                **_scope_values(event),
            )
        )

    async def _run(self, session: AsyncSession, event: StoredEvent) -> None:
        view = await session.get(KernelRunViewORM, event.run_id)
        if event.type == "RunStarted":
            if view is not None:
                raise RuntimeError("RunStarted projection already exists")
            view = KernelRunViewORM(
                id=event.run_id,
                workflow=str(event.public_payload["workflow"]),
                title=str(event.public_payload.get("title", "")),
                status=str(event.public_payload.get("status", RunStatus.RUNNING.value)),
                current_turn=0,
                wait_reason=None,
                stream_version=event.version,
                created_at=event.occurred_at,
                updated_at=event.occurred_at,
                deleted_at=None,
                purge_after=None,
                **_scope_values(event),
            )
            session.add(view)
            return
        if view is None:
            raise RuntimeError("projection received an event before RunStarted")
        if event.type == "PromptAccepted":
            view.status = RunStatus.RUNNING.value
            view.current_turn += 1
        elif event.type == "RunWaiting":
            view.status = RunStatus.WAITING.value
            view.wait_reason = str(event.public_payload.get("reason") or "") or None
        elif event.type == "RunResumed":
            view.status = RunStatus.RUNNING.value
            view.wait_reason = None
        elif event.type == "TurnCompleted":
            view.status = RunStatus.IDLE.value
            view.wait_reason = None
        elif event.type == "RunCompleted":
            view.status = RunStatus.COMPLETED.value
            view.wait_reason = None
        elif event.type == "RunFailed":
            view.status = RunStatus.FAILED.value
            view.wait_reason = None
        elif event.type == "RunCancelled":
            view.status = RunStatus.CANCELLED.value
            view.wait_reason = None
        elif event.type == "RunArchived":
            view.status = RunStatus.ARCHIVED.value
            view.deleted_at = event.occurred_at
            purge_after = event.public_payload.get("purge_after")
            view.purge_after = (
                datetime.fromisoformat(str(purge_after)) if purge_after is not None else None
            )
        elif event.type == "RunRestored":
            view.status = RunStatus.IDLE.value
            view.deleted_at = None
            view.purge_after = None
        elif event.type == "RunPurged":
            view.status = RunStatus.PURGED.value
            view.wait_reason = None
            view.purge_after = event.occurred_at
        view.stream_version = event.version
        view.updated_at = event.occurred_at

    async def _messages(
        self,
        session: AsyncSession,
        event: StoredEvent,
        private_payload: dict[str, object],
    ) -> None:
        role = None
        content = None
        if event.type == "PromptAccepted":
            role = "user"
            content = private_payload.get("prompt", "")
        elif event.type == "AssistantMessageCreated":
            role = "assistant"
            content = private_payload.get("content", "")
        elif event.type == "ToolResultRecorded":
            role = "tool"
            content = private_payload.get("result")
        if role is None:
            return
        session.add(
            KernelMessageViewORM(
                id=event.event_id,
                run_id=event.run_id,
                event_version=event.version,
                role=role,
                content=str(content),
                created_at=event.occurred_at,
                **_scope_values(event),
            )
        )

    async def _effects(self, session: AsyncSession, event: StoredEvent) -> None:
        payload = event.public_payload
        if event.type == "EffectRequested":
            effect_id = UUID(str(payload["effect_id"]))
            session.add(
                KernelEffectViewORM(
                    id=effect_id,
                    run_id=event.run_id,
                    effect_type=str(payload["effect_type"]),
                    safety=str(payload["safety"]),
                    status="blocked" if payload.get("blocked") else "ready",
                    approval_id=(
                        UUID(str(payload["approval_id"])) if payload.get("approval_id") else None
                    ),
                    public_summary=dict(payload.get("summary") or {}),
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                    **_scope_values(event),
                )
            )
            return
        if event.type == "EffectReleased":
            effect_id = UUID(str(payload["effect_id"]))
            view = await session.get(KernelEffectViewORM, effect_id)
            if view is None:
                raise RuntimeError("Effect release projection has no requested Effect")
            view.status = "ready"
            view.updated_at = event.occurred_at
            return
        if event.type not in {
            "EffectSucceeded",
            "EffectFailed",
            "EffectCancelled",
            "EffectOutcomeUnknown",
        }:
            return
        effect_id = UUID(str(payload["effect_id"]))
        view = await session.get(KernelEffectViewORM, effect_id)
        if view is None:
            raise RuntimeError("Effect outcome projection has no requested Effect")
        view.status = {
            "EffectSucceeded": "succeeded",
            "EffectFailed": "failed",
            "EffectCancelled": "cancelled",
            "EffectOutcomeUnknown": "unknown",
        }[event.type]
        view.updated_at = event.occurred_at

    async def _approvals(self, session: AsyncSession, event: StoredEvent) -> None:
        payload = event.public_payload
        if event.type in {"ApprovalDecided", "ApprovalExpired", "ApprovalCancelled"}:
            raw_approval_id = payload.get("approval_id")
            if raw_approval_id is None:
                return
            approval = await session.get(KernelApprovalViewORM, UUID(str(raw_approval_id)))
            if approval is None:
                raise RuntimeError("approval outcome projection has no pending approval")
            if approval.status != "pending":
                raise RuntimeError("approval already has a terminal decision")
            if event.type == "ApprovalDecided":
                approval.status = "decided"
                approval.decision = str(payload["decision"])
                approval.feedback = str(payload.get("feedback") or "") or None
                approval.decided_by_user_id = str(payload["decided_by_user_id"])
            elif event.type == "ApprovalExpired":
                approval.status = "expired"
                approval.decision = "expired"
            else:
                approval.status = "cancelled"
                approval.decision = "cancelled"
            approval.decided_at = event.occurred_at
            return
        if event.type != "ApprovalRequested":
            return
        approval_id = UUID(str(payload["approval_id"]))
        raw_expires_at = payload.get("expires_at")
        expires_at = (
            datetime.fromisoformat(str(raw_expires_at)) if raw_expires_at is not None else None
        )
        session.add(
            KernelApprovalViewORM(
                id=approval_id,
                run_id=event.run_id,
                effect_id=UUID(str(payload["effect_id"])),
                subject=str(payload.get("subject", "")),
                risk_summary=dict(payload.get("risk_summary") or {}),
                status="pending",
                decision=None,
                feedback=None,
                decided_by_user_id=None,
                requested_at=event.occurred_at,
                expires_at=expires_at,
                decided_at=None,
                **_scope_values(event),
            )
        )
        # Projection models stay intentionally relationship-free; persist the
        # FK parent before materializing the frozen reviewer set.
        await session.flush()
        for reviewer in payload.get("reviewer_user_ids", []):
            session.add(
                KernelApprovalReviewerORM(
                    approval_id=approval_id,
                    user_id=str(reviewer),
                )
            )
            session.add(
                KernelNotificationViewORM(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"kernel-approval-notification:{approval_id}:{reviewer}",
                    ),
                    user_id=str(reviewer),
                    kind="approval_requested",
                    payload={"approval_id": str(approval_id), "run_id": str(event.run_id)},
                    read=False,
                    created_at=event.occurred_at,
                )
            )

    async def _resource_build(self, session: AsyncSession, event: StoredEvent) -> None:
        payload = event.public_payload
        view = await session.get(KernelResourceBuildViewORM, event.run_id)
        if event.type == "KnowledgeCandidateCreated":
            if view is not None:
                raise RuntimeError("knowledge build projection already exists")
            session.add(
                KernelResourceBuildViewORM(
                    run_id=event.run_id,
                    knowledge_base_id=str(payload["knowledge_base_id"]),
                    candidate_version_id=str(payload["candidate_version_id"]),
                    stage=str(payload["stage"]),
                    status="running",
                    active_version_id=(
                        str(payload["active_version_id"])
                        if payload.get("active_version_id") is not None
                        else None
                    ),
                    manifest_digest=None,
                    updated_at=event.occurred_at,
                    **_scope_values(event),
                )
            )
            return
        if view is None:
            return
        if event.type == "KnowledgeStageCompleted" and payload.get("next_stage"):
            view.stage = str(payload["next_stage"])
        elif event.type == "KnowledgeVersionPublished":
            view.status = "completed"
            view.active_version_id = str(payload["version_id"])
            view.manifest_digest = str(payload["manifest_digest"])
        elif event.type == "KnowledgeCandidateFailed":
            view.status = "failed"
        view.updated_at = event.occurred_at

    async def _knowledge_publication(
        self,
        session: AsyncSession,
        event: StoredEvent,
    ) -> None:
        payload = event.public_payload
        if event.type == "KnowledgeVersionPublished":
            version_id = UUID(str(payload["version_id"]))
            kb_id = UUID(str(payload["knowledge_base_id"]))
            version = await session.get(KnowledgeVersionORM, version_id)
            kb = await session.get(KnowledgeBaseORM, kb_id)
            if version is None or kb is None:
                raise RuntimeError("knowledge publication target is missing")
            if version.knowledge_base_id != kb.id or version.state != "candidate":
                raise RuntimeError("knowledge publication target is not a candidate")
            version.state = "published"
            version.manifest_digest = str(payload["manifest_digest"])
            version.published_at = event.occurred_at
            kb.active_version_id = version.id
            kb.updated_at = event.occurred_at
        elif event.type == "KnowledgeCandidateFailed":
            version = await session.get(
                KnowledgeVersionORM,
                UUID(str(payload["candidate_version_id"])),
            )
            if version is not None and version.state == "candidate":
                version.state = "failed"
