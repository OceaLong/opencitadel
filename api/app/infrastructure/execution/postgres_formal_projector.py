"""PostgreSQL formal projections derived solely from ``execution_events``."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import case, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution import activity_types
from app.application.execution.outbox_dispatcher import APPROVAL_NOTICE_DESTINATION
from app.application.ports.execution import (
    ApprovalNotifierPort,
    ApprovalWaitingNotice,
    FormalProjectorResult,
)
from app.domain.execution.events import StoredEvent
from app.domain.execution.run import (
    RunAggregate,
    RunFamily,
    RunState,
    RunStatus,
    validated_run_policy_snapshot,
)
from app.domain.execution.serialization import (
    canonical_json_bytes,
    canonical_state_hash,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.notification import Notification
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.models import (
    ExecutionActivityProjectionORM,
    ExecutionApprovalProjectionORM,
    ExecutionOutboxORM,
    ExecutionProjectorCheckpointORM,
    ExecutionPublicEventORM,
    ExecutionResourceBuildProjectionORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM
from app.infrastructure.models.notification import NotificationModel
from app.infrastructure.models.patrol import (
    PatrolPackModel,
    PatrolRemediationModel,
    PatrolRunModel,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.team import TeamMemberORM
from app.infrastructure.observability.execution_metrics import record_replay_failure
from app.infrastructure.repositories.db_notification_repository import DBNotificationRepository
from app.infrastructure.security.db_authorization import configure_session_authorization

logger = logging.getLogger(__name__)

_CHECKPOINT_NAME = "formal"
_CHECKPOINT_SCHEMA_VERSION = 1


class PostgresApprovalNotifier:
    """Persist an ``approval_waiting`` notification and publish a realtime hint.

    Since K4-2 this runs from the outbox dispatcher, not the projection
    transaction: the projector durably records each notice as an outbox row and
    the dispatcher redelivers through this notifier until it succeeds. The
    kernel's system authorization scope is reused so the cross-tenant insert
    into ``notifications`` passes row-level security.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
        publisher: object | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization
        self._publisher = publisher

    @staticmethod
    def _build_notification(notice: ApprovalWaitingNotice, user_id: str) -> Notification:
        # Literal i18n_key values: the quality-baseline contract scans the AST
        # for constant i18n_key keywords to keep contracts/i18n-runtime-keys.json
        # in sync with the UI catalog.
        if notice.kind == "clarification_waiting":
            return Notification(
                user_id=user_id,
                type="clarification_waiting",
                message=f"任务等待你的选择：{notice.subject_label}",
                i18n_key="notifications.clarificationWaiting",
                i18n_params={"subject": notice.subject_label},
                session_id=notice.session_id,
                approval_id=str(notice.approval_id),
            )
        if notice.kind == "clarification_expired":
            return Notification(
                user_id=user_id,
                type="clarification_expired",
                message="澄清超时未选择，任务已取消"
                + (f"：{notice.subject_label}" if notice.subject_label else ""),
                i18n_key="notifications.clarificationExpired",
                i18n_params={"subject": notice.subject_label},
                session_id=notice.session_id,
                approval_id=str(notice.approval_id),
            )
        if notice.kind == "approval_expired":
            return Notification(
                user_id=user_id,
                type="approval_expired",
                message="审批超时未处理，Run 已取消"
                + (f"：{notice.subject_label}" if notice.subject_label else ""),
                i18n_key="notifications.approvalExpired",
                i18n_params={"subject": notice.subject_label},
                session_id=notice.session_id,
                approval_id=str(notice.approval_id),
            )
        return Notification(
            user_id=user_id,
            type="approval_waiting",
            message=f"审批等待处理：{notice.subject_label}",
            i18n_key="notifications.approvalWaiting",
            i18n_params={"subject": notice.subject_label},
            session_id=notice.session_id,
            approval_id=str(notice.approval_id),
        )

    async def approval_waiting(self, notice: ApprovalWaitingNotice) -> None:
        notifications: list[Notification] = []
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            recipients = (
                [notice.user_id]
                if notice.user_id
                else await self._team_reviewers(session, notice.team_id)
            )
            if not recipients:
                logger.warning("审批通知无可用接收人 run=%s team=%s", notice.run_id, notice.team_id)
                return
            repository = DBNotificationRepository(session)
            for user_id in recipients:
                notification = self._build_notification(notice, user_id)
                await repository.save(notification)
                notifications.append(notification)
            await session.commit()
        if self._publisher is not None:
            for notification in notifications:
                try:
                    await self._publisher.publish(
                        notification.user_id,
                        json.dumps(notification.model_dump(mode="json")),
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("审批通知 Redis 发布失败 user=%s: %s", notification.user_id, exc)

    @staticmethod
    async def _team_reviewers(session: AsyncSession, team_id: str | None) -> list[str]:
        """Reviewers for a purely team-owned approval: owners/admins, else everyone."""
        if not team_id:
            return []
        rows = (
            await session.execute(
                select(TeamMemberORM.user_id, TeamMemberORM.role).where(
                    TeamMemberORM.team_id == team_id
                )
            )
        ).all()
        privileged = [user_id for user_id, role in rows if role in ("owner", "admin")]
        return privileged or [user_id for user_id, _role in rows]


@dataclass
class _RunTracker:
    """In-memory fold of one Run's projection across a projection batch (P2-17).

    The batch evolves the Run state event by event but writes the run
    projection row (and its derived session / resource-build rows) only once,
    after the Run's last event in the batch — collapsing O(events) round trips
    into O(runs). The previous row is read and hash-verified exactly once.
    """

    state: RunState
    created_at: datetime
    last_event: StoredEvent


class PostgresFormalProjector:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization
        self._aggregate = RunAggregate()

    async def run_once(
        self,
        owner_scope: OwnerScope,
        *,
        limit: int,
        through_position: int | None = None,
        notify: bool = True,
    ) -> FormalProjectorResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        key, owner_user_id, team_id = self._scope_parts(owner_scope)
        notices: list[tuple[ApprovalWaitingNotice, int]] = []
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                # P2-18: never queue behind another worker's in-flight pass on
                # the same scope — yield this round and let the caller move on.
                if not await self._try_lock(session, key):
                    return FormalProjectorResult(processed=0, last_position=0, busy=True)
                checkpoint, checkpoint_busy = await self._checkpoint(session, key)
                if checkpoint_busy:
                    return FormalProjectorResult(processed=0, last_position=0, busy=True)
                last_position = checkpoint.last_position if checkpoint else 0
                events = await PostgresEventStore(
                    session,
                    event_registries={"run": self._aggregate.event_registry},
                ).read_scope(
                    after_position=last_position,
                    limit=limit,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    through_position=through_position,
                )
                trackers: dict[str, _RunTracker] = {}
                for event in events:
                    await self._project_event(session, event, notices, trackers)
                    last_position = event.position
                for tracker in trackers.values():
                    await self._flush_run(session, tracker)
                # Approval notices are persisted as outbox rows inside the
                # projection transaction (K4-2): a crash after commit can no
                # longer lose a reviewer ping, and the dedupe_key unique
                # constraint keeps replays from duplicating one. A rebuild
                # (notify=False) replays history and must not re-notify.
                if notify:
                    for notice, event_position in notices:
                        await self._write_notice_outbox(session, notice, event_position)
                self._write_checkpoint(
                    session,
                    checkpoint=checkpoint,
                    key=key,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    last_position=last_position,
                )
                await session.commit()
            except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                await session.rollback()
                raise
        return FormalProjectorResult(
            processed=len(events),
            last_position=last_position,
        )

    async def rebuild(
        self,
        owner_scope: OwnerScope,
        *,
        through_position: int | None = None,
        batch_size: int = 1000,
    ) -> FormalProjectorResult:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        key, owner_user_id, team_id = self._scope_parts(owner_scope)
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                # The destructive delete phase must run: block on the scope
                # lock instead of yielding (the kernel's regular passes are
                # short, and a rebuilding scope is excluded from discovery).
                await self._lock(session, key)
                target = through_position
                if target is None:
                    target = await PostgresEventStore(session).latest_scope_position(
                        owner_user_id=owner_user_id,
                        team_id=team_id,
                    )
                await self._delete_scope(
                    session,
                    key=key,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                )
                await session.commit()
            except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                await session.rollback()
                raise

        processed = 0
        last_position = 0
        while True:
            batch = await self.run_once(
                owner_scope,
                limit=batch_size,
                through_position=target,
                notify=False,
            )
            if batch.busy:
                await asyncio.sleep(0.1)
                continue
            processed += batch.processed
            last_position = batch.last_position
            if batch.processed == 0:
                break
        return FormalProjectorResult(
            processed=processed,
            last_position=last_position,
        )

    async def _project_event(
        self,
        session: AsyncSession,
        event: StoredEvent,
        notices: list[tuple[ApprovalWaitingNotice, int]],
        trackers: dict[str, _RunTracker],
    ) -> None:
        run_state: RunState | None = None
        approval_kind: str | None = None
        if event.stream_type == "run":
            tracker = await self._track_run(session, event, trackers)
            run_state = tracker.state
            await self._project_activity(session, event, run_state)
            await self._project_approval(session, event, run_state)
            await self._project_product_lifecycle(session, event, run_state)
            await self._project_resource_build_failure(session, event, run_state)
            approval_kind = await self._approval_kind(session, event)
            notices.extend(
                (notice, event.position)
                for notice in self._collect_approval_notice(event, run_state, approval_kind)
            )
        await self._project_public_event(session, event, run_state, approval_kind)

    async def _approval_kind(
        self,
        session: AsyncSession,
        event: StoredEvent,
    ) -> str | None:
        """The approval's kind, for shaping decided/expired public events.

        Requested events carry it in the payload; decided/expired events only
        carry the approval_id, so the kind comes from the approval projection
        row written when the request was projected (same scope, earlier
        position — always present by the time its settlement is processed).
        """
        if event.event_type == "ApprovalRequested":
            return str(event.public_payload.get("approval_kind") or "tool_effect")
        if event.event_type not in ("ApprovalDecided", "ApprovalExpired"):
            return None
        record = await session.get(
            ExecutionApprovalProjectionORM,
            UUID(str(event.public_payload["approval_id"])),
        )
        return record.approval_kind if record is not None else "tool_effect"

    async def _track_run(
        self,
        session: AsyncSession,
        event: StoredEvent,
        trackers: dict[str, _RunTracker],
    ) -> _RunTracker:
        tracker = trackers.get(event.stream_id)
        if tracker is None:
            run_id = UUID(event.stream_id)
            existing = await session.get(ExecutionRunProjectionORM, run_id)
            if existing is None:
                previous = self._aggregate.initial_state(event.stream_id)
                created_at = event.occurred_at
            else:
                previous = RunState.model_validate(existing.state)
                if canonical_state_hash(previous) != existing.state_hash:
                    record_replay_failure("projection_hash_mismatch")
                    raise ValueError("execution_run_projection state hash mismatch")
                created_at = existing.created_at
            tracker = _RunTracker(state=previous, created_at=created_at, last_event=event)
            trackers[event.stream_id] = tracker
        tracker.state = self._aggregate.evolve(tracker.state, event)
        tracker.last_event = event
        return tracker

    async def _write_notice_outbox(
        self,
        session: AsyncSession,
        notice: ApprovalWaitingNotice,
        event_position: int,
    ) -> None:
        scope = f"user:{notice.user_id}" if notice.user_id else f"team:{notice.team_id}"
        dedupe_key = (
            f"{APPROVAL_NOTICE_DESTINATION}:"
            f"{uuid5(NAMESPACE_URL, f'opencitadel:{notice.approval_id}:{notice.kind}:{scope}')}"
        )
        await session.execute(
            pg_insert(ExecutionOutboxORM)
            .values(
                outbox_id=uuid4(),
                event_position=event_position,
                destination=APPROVAL_NOTICE_DESTINATION,
                dedupe_key=dedupe_key,
                payload={
                    "user_id": notice.user_id,
                    "approval_id": str(notice.approval_id),
                    "run_id": str(notice.run_id),
                    "session_id": notice.session_id,
                    "subject_label": notice.subject_label,
                    "team_id": notice.team_id,
                    "kind": notice.kind,
                },
                owner_user_id=notice.user_id,
                team_id=notice.team_id if notice.user_id is None else None,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )

    @staticmethod
    def _collect_approval_notice(
        event: StoredEvent,
        run_state: RunState,
        approval_kind: str | None,
    ) -> tuple[ApprovalWaitingNotice, ...]:
        if event.event_type not in ("ApprovalRequested", "ApprovalExpired"):
            return ()
        # Personal-scope runs notify their owner (the initiating user). A purely
        # team-owned Run (owner_user_id is None) carries the team id instead so
        # the notifier can fan out to the team's reviewers — otherwise team
        # approvals sit silent until they expire.
        user_id = event.owner_user_id
        if not user_id and not event.team_id:
            return ()
        payload = event.public_payload
        session_id = (
            run_state.source_entity_id if run_state.source_entity_type == "session" else None
        )
        # Clarifications are conversation interactions, not governance
        # approvals: they get their own notification wording and never look
        # like a pending review (澄清不是审批).
        clarification = approval_kind == "clarification"
        if event.event_type == "ApprovalExpired":
            kind = "clarification_expired" if clarification else "approval_expired"
        else:
            kind = "clarification_waiting" if clarification else "approval_waiting"
        subject = str(payload.get("subject_label") or "")
        if clarification and event.event_type == "ApprovalRequested":
            # For the clarification card the meaningful subject is the question.
            subject = str(payload.get("risk_summary") or subject)[:128]
        return (
            ApprovalWaitingNotice(
                user_id=user_id,
                approval_id=UUID(str(payload["approval_id"])),
                run_id=run_state.run_id,
                session_id=session_id,
                subject_label=subject,
                team_id=None if user_id else event.team_id,
                kind=kind,
            ),
        )

    async def _flush_run(
        self,
        session: AsyncSession,
        tracker: _RunTracker,
    ) -> None:
        """UPSERT one Run's projection row from its batch-final state (P2-17).

        All intermediate states within the batch share this transaction and
        were never visible outside it, so persisting only the final fold is
        observably identical to the previous per-event UPSERTs.
        """
        state = tracker.state
        event = tracker.last_event
        created_at = tracker.created_at
        if state.family is None:
            raise ValueError("Run projection cannot persist an uncreated Run")
        policy_snapshot = validated_run_policy_snapshot(state)
        state_json = state.model_dump(mode="json")
        values = {
            "run_id": state.run_id,
            "family": state.family.value,
            "source_entity_type": state.source_entity_type,
            "source_entity_id": state.source_entity_id,
            "execution_policy_revision_id": policy_snapshot.execution_revision_id,
            "execution_policy_digest": policy_snapshot.execution_policy_digest,
            "status": state.status.value,
            "terminal": state.status
            in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
            # Decision-readiness columns (D4): load_ready filters on these in
            # SQL. ``decision_due_at`` is armed whenever this event leaves the
            # Run in a state the decision planner must look at — queued, or
            # running with no active activities. WAITING never arms it:
            # approvals resume via DecideApproval/ExpireApproval commands and
            # retry-waits are driven by the durable retry timer (K2-4), both of
            # which produce new events that re-arm the row here.
            "wait_reason": state.wait_reason,
            "active_activity_count": len(state.active_activity_ids),
            "decision_due_at": (
                event.occurred_at
                if state.status == RunStatus.QUEUED
                or (state.status == RunStatus.RUNNING and not state.active_activity_ids)
                else None
            ),
            "parent_run_id": state.parent_run_id,
            "correlation_id": state.correlation_id,
            "owner_user_id": event.owner_user_id,
            "team_id": event.team_id,
            "stream_version": state.stream_version,
            "last_event_position": event.position,
            "state": state_json,
            "state_hash": canonical_state_hash(state),
            "last_event_hash": event.event_hash,
            "created_at": created_at,
            "updated_at": event.occurred_at,
            "terminal_at": (
                event.occurred_at
                if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
                else None
            ),
        }
        await session.execute(
            pg_insert(ExecutionRunProjectionORM)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={key: value for key, value in values.items() if key != "run_id"},
            )
        )
        if state.source_entity_type == "session" and state.source_entity_id:
            session_status = {
                RunStatus.NEW: "pending",
                RunStatus.QUEUED: "pending",
                RunStatus.RUNNING: "running",
                RunStatus.WAITING: "waiting",
                RunStatus.COMPLETED: "completed",
                RunStatus.FAILED: "failed",
                RunStatus.CANCELLED: "cancelled",
            }[state.status]
            scope_filter = (
                SessionModel.owner_user_id == event.owner_user_id
                if event.owner_user_id is not None
                else SessionModel.team_id == event.team_id
            )
            session_values: dict[str, object] = {
                "status": session_status,
                # Optimistic monotonic guard: never let a replayed/out-of-order
                # older event regress the session's execution status. Paired with
                # the WHERE last_event_position guard below.
                "last_event_position": event.position,
            }
            if state.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                session_values.update(
                    active_execution_run_id=None,
                    active_execution_request_id=None,
                )
            else:
                session_values["active_execution_run_id"] = state.run_id
            await session.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == state.source_entity_id,
                    scope_filter,
                    or_(
                        SessionModel.active_execution_run_id.is_(None),
                        SessionModel.active_execution_run_id == state.run_id,
                    ),
                    or_(
                        SessionModel.last_event_position.is_(None),
                        SessionModel.last_event_position < event.position,
                    ),
                )
                .values(**session_values)
            )
        await self._flush_resource_build(session, event, state)

    async def _project_product_lifecycle(
        self,
        session: AsyncSession,
        event: StoredEvent,
        state: RunState,
    ) -> None:
        if event.event_type not in {"RunFailed", "RunCancelled"}:
            return
        pack_scope = select(PatrolPackModel.id)
        if event.owner_user_id is not None:
            pack_scope = pack_scope.where(
                PatrolPackModel.owner_user_id == event.owner_user_id,
                PatrolPackModel.team_id.is_(None),
            )
        elif event.team_id is not None:
            pack_scope = pack_scope.where(
                PatrolPackModel.team_id == event.team_id,
            )
        else:
            raise ValueError("product projection requires an owner scope")

        failure_code = str(
            event.public_payload.get("failure_code")
            or event.public_payload.get("reason")
            or "EXECUTION_TERMINATED"
        )
        if (
            state.family == RunFamily.PATROL
            and state.source_entity_type == "patrol_pack_validation"
            and state.source_entity_id
        ):
            await session.execute(
                update(PatrolPackModel)
                .where(
                    PatrolPackModel.id == state.source_entity_id,
                    PatrolPackModel.id.in_(pack_scope),
                    PatrolPackModel.status == "validating",
                    PatrolPackModel.validation_run_id == str(state.run_id),
                    or_(
                        PatrolPackModel.last_event_position.is_(None),
                        PatrolPackModel.last_event_position < event.position,
                    ),
                )
                .values(
                    status="invalid",
                    last_validated_at=event.occurred_at,
                    last_validated_version=None,
                    validation_run_id=None,
                    validation_summary={
                        "ok": False,
                        "errors": [f"Validation Run terminated: {failure_code}"],
                        "validation_run_id": str(state.run_id),
                        "failure_code": failure_code,
                        "capability_hash": None,
                        "enabled_tools": [],
                        "dry_run": {},
                    },
                    last_event_position=event.position,
                    updated_at=event.occurred_at,
                )
            )
            return
        if (
            state.family == RunFamily.PATROL
            and state.source_entity_type == "patrol_run"
            and state.source_entity_id
        ):
            product_status = "cancelled" if event.event_type == "RunCancelled" else "failed"
            await session.execute(
                update(PatrolRunModel)
                .where(
                    PatrolRunModel.id == state.source_entity_id,
                    PatrolRunModel.pack_id.in_(pack_scope),
                    PatrolRunModel.status.in_(("queued", "running")),
                    or_(
                        PatrolRunModel.last_event_position.is_(None),
                        PatrolRunModel.last_event_position < event.position,
                    ),
                )
                .values(
                    status=product_status,
                    finished_at=event.occurred_at,
                    summary={
                        "error_code": failure_code,
                        "error_message": (
                            "Formal execution terminated before Patrol result finalization"
                        ),
                    },
                    last_event_position=event.position,
                    updated_at=event.occurred_at,
                )
            )
            return

        if state.family != RunFamily.REMEDIATION:
            return
        remediation_id = state.semantic_payload.get("remediation_id")
        if not isinstance(remediation_id, str) or not remediation_id:
            raise ValueError("Remediation Run is missing remediation_id")
        owned_patrol_runs = select(PatrolRunModel.id).where(PatrolRunModel.pack_id.in_(pack_scope))
        product_status = case(
            (
                PatrolRemediationModel.status == "proposed",
                "cancelled",
            ),
            else_="failed",
        )
        await session.execute(
            update(PatrolRemediationModel)
            .where(
                PatrolRemediationModel.id == remediation_id,
                PatrolRemediationModel.run_id.in_(owned_patrol_runs),
                PatrolRemediationModel.status.in_(("proposed", "executing")),
                or_(
                    PatrolRemediationModel.last_event_position.is_(None),
                    PatrolRemediationModel.last_event_position < event.position,
                ),
            )
            .values(
                status=product_status,
                error_code=failure_code,
                error_message=(
                    "Formal remediation execution terminated before a durable outcome was recorded"
                ),
                last_event_position=event.position,
                updated_at=event.occurred_at,
            )
        )

    async def _project_activity(
        self,
        session: AsyncSession,
        event: StoredEvent,
        run_state: RunState,
    ) -> None:
        if event.event_type not in {
            "ActivityRequested",
            "ActivityCallStarted",
            "ActivityCompleted",
            "ActivityFailed",
            "ActivityOutcomeUnknown",
            "ActivityCancelled",
        }:
            return
        activity_id = UUID(str(event.public_payload["activity_id"]))
        existing = await session.get(ExecutionActivityProjectionORM, activity_id)
        if event.event_type == "ActivityRequested":
            status = "pending"
            activity_type = str(event.public_payload["activity_type"])
            created_at = event.occurred_at
            attempt = 0
        else:
            if existing is None:
                raise ValueError("Activity projection is missing its request")
            activity_type = existing.activity_type
            created_at = existing.created_at
            attempt = existing.attempt
            status = {
                "ActivityCallStarted": "running",
                "ActivityCompleted": "succeeded",
                "ActivityFailed": "failed",
                "ActivityOutcomeUnknown": "unknown",
                "ActivityCancelled": "cancelled",
            }[event.event_type]
            if event.event_type == "ActivityCallStarted":
                attempt += 1
        terminal = status in {"succeeded", "failed", "unknown", "cancelled"}
        state_json = {
            "activity_id": str(activity_id),
            "run_id": str(run_state.run_id),
            "activity_type": activity_type,
            "status": status,
            "generation": int(event.public_payload.get("generation", 0)),
        }
        values = {
            "activity_id": activity_id,
            "run_id": run_state.run_id,
            "activity_type": activity_type,
            "status": status,
            "attempt": attempt,
            "generation": int(event.public_payload.get("generation", 0)),
            "result_summary": event.public_payload.get("result_summary"),
            "failure_code": event.public_payload.get("failure_code"),
            "owner_user_id": event.owner_user_id,
            "team_id": event.team_id,
            "stream_version": event.stream_version,
            "last_event_position": event.position,
            "state": state_json,
            "state_hash": self._json_hash(state_json),
            "created_at": created_at,
            "updated_at": event.occurred_at,
            "terminal_at": event.occurred_at if terminal else None,
        }
        await session.execute(
            pg_insert(ExecutionActivityProjectionORM)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["activity_id"],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"activity_id", "created_at"}
                },
            )
        )

    @staticmethod
    async def _project_approval(
        session: AsyncSession,
        event: StoredEvent,
        run_state: RunState,
    ) -> None:
        if event.event_type == "ApprovalRequested":
            payload = event.public_payload
            values = {
                "approval_id": UUID(str(payload["approval_id"])),
                "run_id": run_state.run_id,
                "source_entity_type": run_state.source_entity_type,
                "source_entity_id": run_state.source_entity_id,
                "approval_kind": str(payload["approval_kind"]),
                "subject_activity_id": UUID(str(payload["subject_activity_id"])),
                "subject_label": str(payload["subject_label"]),
                "risk_summary": str(payload["risk_summary"]),
                "status": "pending",
                "decision": None,
                "decided_by_user_id": None,
                "feedback": "",
                "owner_user_id": event.owner_user_id,
                "team_id": event.team_id,
                "request_event_position": event.position,
                "decision_event_position": None,
                "requested_at": event.occurred_at,
                "decided_at": None,
            }
            await session.execute(
                pg_insert(ExecutionApprovalProjectionORM)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["approval_id"],
                    set_={key: value for key, value in values.items() if key != "approval_id"},
                )
            )
            return
        if event.event_type == "ApprovalDecided":
            payload = event.public_payload
            decision = str(payload["decision"])
            approval_id = UUID(str(payload["approval_id"]))
            await session.execute(
                update(ExecutionApprovalProjectionORM)
                .where(
                    ExecutionApprovalProjectionORM.approval_id == approval_id,
                    ExecutionApprovalProjectionORM.run_id == run_state.run_id,
                    ExecutionApprovalProjectionORM.status == "pending",
                )
                .values(
                    status=decision,
                    decision=decision,
                    decided_by_user_id=str(payload["actor_user_id"]),
                    feedback=str(payload.get("feedback") or ""),
                    decision_event_position=event.position,
                    decided_at=event.occurred_at,
                )
            )
            await PostgresFormalProjector._mark_approval_notifications_read(session, [approval_id])
            return
        if event.event_type == "ApprovalExpired":
            payload = event.public_payload
            approval_id = UUID(str(payload["approval_id"]))
            await session.execute(
                update(ExecutionApprovalProjectionORM)
                .where(
                    ExecutionApprovalProjectionORM.approval_id == approval_id,
                    ExecutionApprovalProjectionORM.run_id == run_state.run_id,
                    ExecutionApprovalProjectionORM.status == "pending",
                )
                .values(
                    status="expired",
                    decision="expired",
                    decided_by_user_id=None,
                    feedback="",
                    decision_event_position=event.position,
                    decided_at=event.occurred_at,
                )
            )
            await PostgresFormalProjector._mark_approval_notifications_read(session, [approval_id])
            return
        if event.event_type in {"RunCompleted", "RunFailed", "RunCancelled"}:
            # An expiry emits ApprovalExpired *then* RunCancelled; the approval is
            # already 'expired' (no longer pending) by the time this runs, so this
            # blanket cancel only touches approvals still genuinely pending.
            cancelled_ids = list(
                (
                    await session.execute(
                        update(ExecutionApprovalProjectionORM)
                        .where(
                            ExecutionApprovalProjectionORM.run_id == run_state.run_id,
                            ExecutionApprovalProjectionORM.status == "pending",
                        )
                        .values(
                            status="cancelled",
                            decision="cancelled",
                            decided_by_user_id=None,
                            feedback="",
                            decision_event_position=event.position,
                            decided_at=event.occurred_at,
                        )
                        .returning(ExecutionApprovalProjectionORM.approval_id)
                    )
                ).scalars()
            )
            await PostgresFormalProjector._mark_approval_notifications_read(session, cancelled_ids)

    async def _flush_resource_build(
        self,
        session: AsyncSession,
        event: StoredEvent,
        state: RunState,
    ) -> None:
        """UPSERT the resource-build row from the Run's batch-final state (P2-17)."""
        if state.family is not RunFamily.KB_INGEST:
            return
        build_id = str(state.semantic_payload.get("build_id") or state.source_entity_id)
        resource_kind = "knowledge_base"
        progress = {
            RunStatus.NEW: 0,
            RunStatus.QUEUED: 0,
            RunStatus.RUNNING: 25,
            RunStatus.WAITING: 50,
            RunStatus.COMPLETED: 100,
            RunStatus.FAILED: 100,
            RunStatus.CANCELLED: 100,
        }[state.status]
        existing = await session.get(ExecutionResourceBuildProjectionORM, build_id)
        # Fine-grained progress/phase is written off-stream by the activity
        # worker's progress sink; the projector only derives a coarse floor from
        # the Run status and must never regress a fresher off-stream value.
        phase = existing.phase if existing is not None else None
        if existing is not None and not self._is_terminal_status(state.status):
            progress = max(progress, existing.progress)
        values = {
            "build_id": build_id,
            "run_id": state.run_id,
            "resource_kind": resource_kind,
            "resource_id": self._optional_string(state.semantic_payload.get("resource_id"))
            or state.source_entity_id,
            "status": state.status.value,
            "phase": phase,
            "progress": progress,
            "active_version_id": self._optional_string(
                state.semantic_payload.get("active_version_id")
            ),
            "candidate_version_id": self._optional_string(state.semantic_payload.get("version_id")),
            "failure_code": state.failure_code,
            "owner_user_id": event.owner_user_id,
            "team_id": event.team_id,
            "stream_version": state.stream_version,
            "last_event_position": event.position,
            "state": state.model_dump(mode="json"),
            "state_hash": canonical_state_hash(state),
            "created_at": existing.created_at if existing else event.occurred_at,
            "updated_at": event.occurred_at,
            "terminal_at": (
                event.occurred_at
                if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
                else None
            ),
        }
        await session.execute(
            pg_insert(ExecutionResourceBuildProjectionORM)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["build_id"],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"build_id", "created_at"}
                },
            )
        )

    @staticmethod
    async def _mark_approval_notifications_read(
        session: AsyncSession,
        approval_ids: list[UUID],
    ) -> None:
        """Settle the waiting notifications of decided/expired approvals.

        Same transaction as the approval projection update: once an approval
        (or clarification) is no longer pending, its "waiting" notification
        must stop nagging. Idempotent and replay-safe (rebuilds re-mark rows
        that are already read).
        """
        if not approval_ids:
            return
        await session.execute(
            update(NotificationModel)
            .where(
                NotificationModel.approval_id.in_([str(item) for item in approval_ids]),
                NotificationModel.read.is_(False),
            )
            .values(read=True)
        )

    async def _project_resource_build_failure(
        self,
        session: AsyncSession,
        event: StoredEvent,
        state: RunState,
    ) -> None:
        """Per-event terminal side effects on the KB product tables.

        These are event-type conditioned (unlike the state-derived build row,
        which is flushed once per Run per batch), so they stay per event.
        """
        if state.family is not RunFamily.KB_INGEST:
            return
        if event.event_type not in {"RunCancelled", "RunFailed"}:
            return
        build_id = str(state.semantic_payload.get("build_id") or state.source_entity_id)
        resource_id = (
            self._optional_string(state.semantic_payload.get("resource_id"))
            or state.source_entity_id
        )
        error_code = (
            "BUILD_CANCELLED"
            if event.event_type == "RunCancelled"
            else state.failure_code or "BUILD_FAILED"
        )
        await session.execute(
            update(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.build_id == build_id,
                KnowledgeBaseVersionORM.published_at.is_(None),
            )
            .values(state="failed")
        )
        await session.execute(
            update(KnowledgeBaseModel)
            .where(
                KnowledgeBaseModel.id == resource_id,
                or_(
                    KnowledgeBaseModel.last_event_position.is_(None),
                    KnowledgeBaseModel.last_event_position < event.position,
                ),
            )
            .values(
                status=case(
                    (
                        KnowledgeBaseModel.active_version_id.is_not(None),
                        "ready",
                    ),
                    else_="failed",
                ),
                error=error_code,
                last_event_position=event.position,
                updated_at=event.occurred_at,
            )
        )

    @staticmethod
    async def _project_public_event(
        session: AsyncSession,
        event: StoredEvent,
        state: RunState | None,
        approval_kind: str | None = None,
    ) -> None:
        public_shape = PostgresFormalProjector._public_shape(event, approval_kind)
        if public_shape is None:
            return
        event_type, payload = public_shape
        # Dedupe on the source event identity: replays and rebuilds re-insert
        # the same event_id, while the feed's own ``seq`` keeps advancing.
        await session.execute(
            pg_insert(ExecutionPublicEventORM)
            .values(
                position=event.position,
                event_id=event.event_id,
                run_id=state.run_id if state else None,
                source_entity_type=state.source_entity_type if state else None,
                source_entity_id=state.source_entity_id if state else None,
                stream_type=event.stream_type,
                stream_id=event.stream_id,
                stream_version=event.stream_version,
                event_type=event_type,
                payload=payload,
                owner_user_id=event.owner_user_id,
                team_id=event.team_id,
                occurred_at=event.occurred_at,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

    @staticmethod
    def _public_shape(
        event: StoredEvent,
        approval_kind: str | None = None,
    ) -> tuple[str, dict] | None:
        meta = {
            "event_id": str(event.event_id),
            "created_at": int(event.occurred_at.timestamp()),
            "schema_version": 1,
            "visibility": "user",
            "channel": "ui",
            "persist": True,
        }
        payload = event.public_payload
        if event.event_type == "RunCreated":
            public_input = payload.get("input", {})
            if not isinstance(public_input, dict):
                public_input = {}
            return "message", {**meta, **public_input}
        if event.event_type in {"RunStarted", "RunResumed"}:
            return "session_status", {**meta, "status": "running"}
        if event.event_type == "RunWaiting":
            return "session_status", {
                **meta,
                "status": "waiting",
                "reason": payload.get("reason"),
            }
        if event.event_type == "RunCompleted":
            return "done", {**meta, "status": "completed"}
        if event.event_type == "RunCancelled":
            return "session_status", {
                **meta,
                "status": "cancelled",
                "reason": payload.get("reason"),
            }
        if event.event_type in {"RunFailed", "RunAttemptFailed"}:
            failure_code = str(payload.get("failure_code") or "RUN_FAILED")
            return "error", {
                **meta,
                "error": failure_code,
                "code": failure_code,
                "retryable": event.event_type == "RunAttemptFailed",
            }
        if event.event_type == "ApprovalRequested":
            raw_choices = payload.get("choices")
            choices = (
                [str(choice) for choice in raw_choices]
                if isinstance(raw_choices, list) and raw_choices
                else None
            )
            # 澄清不是审批：clarification 走独立的 "ask" 公共事件，不进入
            # 审批卡片/收件箱语义；治理侧的正式审批投影仍完整记录它。
            if str(payload.get("approval_kind") or "tool_effect") == "clarification":
                return "ask", {
                    **meta,
                    "ask_id": str(payload["approval_id"]),
                    "status": "pending",
                    "question": str(payload["risk_summary"]),
                    "choices": choices or [],
                    "tool_name": str(payload["subject_label"]),
                    "subject_activity_id": str(payload["subject_activity_id"]),
                }
            return "approval", {
                **meta,
                "approval_id": str(payload["approval_id"]),
                "kind": "tool",
                "approval_kind": "tool_effect",
                "payload": {
                    "subject_activity_id": str(payload["subject_activity_id"]),
                    "tool_name": str(payload["subject_label"]),
                    "note": str(payload["risk_summary"]),
                },
                "options": ["approve", "reject"],
            }
        if event.event_type == "ApprovalDecided" and approval_kind == "clarification":
            decision = str(payload["decision"])
            return "ask", {
                **meta,
                "ask_id": str(payload["approval_id"]),
                "status": "resolved" if decision == "approved" else "declined",
                "choice": str(payload.get("feedback") or ""),
            }
        if event.event_type == "ApprovalExpired" and approval_kind == "clarification":
            return "ask", {
                **meta,
                "ask_id": str(payload["approval_id"]),
                "status": "expired",
            }
        if event.event_type == "ApprovalDecided":
            return "approval", {
                **meta,
                "approval_id": str(payload["approval_id"]),
                "kind": "tool",
                "payload": {
                    "decision": str(payload["decision"]),
                    "feedback": str(payload.get("feedback") or ""),
                },
                "options": [],
            }
        if event.event_type == "ActivityRequested":
            public_data = payload.get("public_data", {})
            if payload.get("activity_type") == activity_types.TOOL_CALL and isinstance(
                public_data, dict
            ):
                name = str(public_data.get("name") or "tool")
                return "tool", {
                    **meta,
                    "tool_call_id": str(public_data.get("tool_call_id") or ""),
                    "name": name,
                    "function": name,
                    "args": public_data.get("arguments") or {},
                    "status": "started",
                }
            return None
        if event.event_type == "ActivityCompleted":
            public_data = payload.get("public_data", {})
            if not isinstance(public_data, dict):
                return None
            kind = public_data.get("kind")
            if kind == "message":
                return "message", {
                    **meta,
                    "role": str(public_data.get("role") or "assistant"),
                    "message": str(public_data.get("message") or ""),
                }
            if kind == "tool":
                name = str(public_data.get("name") or "tool")
                return "tool", {
                    **meta,
                    "tool_call_id": str(public_data.get("tool_call_id") or ""),
                    "name": name,
                    "function": name,
                    "args": public_data.get("arguments") or {},
                    "status": str(public_data.get("status") or "completed"),
                    "content": public_data.get("content"),
                }
            return None
        return None

    async def _checkpoint(
        self,
        session: AsyncSession,
        key: str,
    ) -> tuple[ExecutionProjectorCheckpointORM | None, bool]:
        """Lock and return the scope checkpoint; (None, True) means "busy".

        P2-18: ``skip_locked`` never queues behind a concurrent holder. A
        missing row is disambiguated from a locked one by a plain re-read: only
        when the row genuinely does not exist may the caller create it.
        """
        checkpoint = await session.scalar(
            select(ExecutionProjectorCheckpointORM)
            .where(
                ExecutionProjectorCheckpointORM.projector_name == _CHECKPOINT_NAME,
                ExecutionProjectorCheckpointORM.owner_scope_key == key,
            )
            .with_for_update(skip_locked=True)
        )
        if checkpoint is None:
            held_elsewhere = await session.scalar(
                select(ExecutionProjectorCheckpointORM.last_position).where(
                    ExecutionProjectorCheckpointORM.projector_name == _CHECKPOINT_NAME,
                    ExecutionProjectorCheckpointORM.owner_scope_key == key,
                )
            )
            if held_elsewhere is not None:
                return None, True
            return None, False
        self._validate_checkpoint(checkpoint, key)
        return checkpoint, False

    @staticmethod
    def _write_checkpoint(
        session: AsyncSession,
        *,
        checkpoint: ExecutionProjectorCheckpointORM | None,
        key: str,
        owner_user_id: str | None,
        team_id: str | None,
        last_position: int,
    ) -> None:
        state = PostgresFormalProjector._checkpoint_state(key, last_position)
        state_hash = PostgresFormalProjector._json_hash(state)
        if checkpoint is None:
            session.add(
                ExecutionProjectorCheckpointORM(
                    projector_name=_CHECKPOINT_NAME,
                    owner_scope_key=key,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    last_position=last_position,
                    state=state,
                    state_hash=state_hash,
                )
            )
            return
        checkpoint.last_position = last_position
        checkpoint.state = state
        checkpoint.state_hash = state_hash
        checkpoint.updated_at = datetime.now(UTC)

    @staticmethod
    def _checkpoint_state(key: str, last_position: int) -> dict:
        return {
            "schema_version": _CHECKPOINT_SCHEMA_VERSION,
            "owner_scope_key": key,
            "last_position": last_position,
        }

    @staticmethod
    def _validate_checkpoint(
        checkpoint: ExecutionProjectorCheckpointORM,
        key: str,
    ) -> None:
        expected_state = PostgresFormalProjector._checkpoint_state(
            key,
            checkpoint.last_position,
        )
        expected_hash = PostgresFormalProjector._json_hash(expected_state)
        if checkpoint.state != expected_state or not hmac.compare_digest(
            checkpoint.state_hash,
            expected_hash,
        ):
            raise ValueError("formal projector checkpoint integrity check failed")

    @staticmethod
    async def _delete_scope(
        session: AsyncSession,
        *,
        key: str,
        owner_user_id: str | None,
        team_id: str | None,
    ) -> None:
        for model in (
            ExecutionActivityProjectionORM,
            ExecutionApprovalProjectionORM,
            ExecutionResourceBuildProjectionORM,
            ExecutionRunProjectionORM,
            ExecutionPublicEventORM,
        ):
            scope_filter = (
                model.owner_user_id == owner_user_id
                if owner_user_id is not None
                else model.team_id == team_id
            )
            await session.execute(delete(model).where(scope_filter))
        await session.execute(
            delete(ExecutionProjectorCheckpointORM).where(
                ExecutionProjectorCheckpointORM.projector_name == _CHECKPOINT_NAME,
                ExecutionProjectorCheckpointORM.owner_scope_key == key,
            )
        )

    @staticmethod
    def _lock_key(key: str) -> int:
        digest = hashlib.sha256(f"{_CHECKPOINT_NAME}\0{key}".encode()).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    async def _lock(session: AsyncSession, key: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": PostgresFormalProjector._lock_key(key)},
        )

    @staticmethod
    async def _try_lock(session: AsyncSession, key: str) -> bool:
        """Non-blocking scope lock (P2-18): False means another pass holds it."""
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": PostgresFormalProjector._lock_key(key)},
        )
        return bool(acquired)

    @staticmethod
    def _scope_parts(
        owner_scope: OwnerScope,
    ) -> tuple[str, str | None, str | None]:
        if owner_scope.type == OwnerScopeType.PERSONAL:
            if owner_scope.team_id is not None:
                raise ValueError("personal scope cannot include team_id")
            return f"user:{owner_scope.user_id}", owner_scope.user_id, None
        if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
            return f"team:{owner_scope.team_id}", None, owner_scope.team_id
        raise ValueError("team scope requires team_id")

    @staticmethod
    def _is_terminal_status(status: RunStatus) -> bool:
        return status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

    @staticmethod
    def _json_hash(value: dict) -> str:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None


__all__ = [
    "ApprovalNotifierPort",
    "ApprovalWaitingNotice",
    "PostgresApprovalNotifier",
    "PostgresFormalProjector",
]
