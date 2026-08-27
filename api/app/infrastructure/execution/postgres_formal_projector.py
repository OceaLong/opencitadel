"""PostgreSQL formal projections derived solely from ``execution_events``."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.execution import FormalProjectorResult
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
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.models import (
    ExecutionActivityProjectionORM,
    ExecutionApprovalProjectionORM,
    ExecutionProjectorCheckpointORM,
    ExecutionPublicEventORM,
    ExecutionResourceBuildProjectionORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.models.codebase import CodebaseModel
from app.infrastructure.models.codebase_version import CodebaseVersionORM
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM
from app.infrastructure.models.patrol import (
    PatrolPackModel,
    PatrolRemediationModel,
    PatrolRunModel,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.security.db_authorization import configure_session_authorization

_CHECKPOINT_NAME = "formal"
_CHECKPOINT_SCHEMA_VERSION = 1


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
    ) -> FormalProjectorResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        key, owner_user_id, team_id = self._scope_parts(owner_scope)
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                await self._lock(session, key)
                checkpoint = await self._checkpoint(session, key)
                last_position = checkpoint.last_position if checkpoint else 0
                events = await PostgresEventStore(session).read_scope(
                    after_position=last_position,
                    limit=limit,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    through_position=through_position,
                )
                for event in events:
                    await self._project_event(session, event)
                    last_position = event.position
                self._write_checkpoint(
                    session,
                    checkpoint=checkpoint,
                    key=key,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    last_position=last_position,
                )
                await session.commit()
            except (OSError, RuntimeError, ValueError):
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
            except (OSError, RuntimeError, ValueError):
                await session.rollback()
                raise

        processed = 0
        last_position = 0
        while True:
            batch = await self.run_once(
                owner_scope,
                limit=batch_size,
                through_position=target,
            )
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
    ) -> None:
        run_state: RunState | None = None
        if event.stream_type == "run":
            run_state = await self._project_run(session, event)
            await self._project_activity(session, event, run_state)
            await self._project_approval(session, event, run_state)
            await self._project_resource_build(session, event, run_state)
        await self._project_public_event(session, event, run_state)

    async def _project_run(
        self,
        session: AsyncSession,
        event: StoredEvent,
    ) -> RunState:
        run_id = UUID(event.stream_id)
        existing = await session.get(ExecutionRunProjectionORM, run_id)
        if existing is None:
            previous = self._aggregate.initial_state(event.stream_id)
            created_at = event.occurred_at
        else:
            previous = RunState.model_validate(existing.state)
            if canonical_state_hash(previous) != existing.state_hash:
                raise ValueError("execution_run_projection state hash mismatch")
            created_at = existing.created_at
        state = self._aggregate.evolve(previous, event)
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
            session_values: dict[str, object] = {"status": session_status}
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
                )
                .values(**session_values)
            )
        await self._project_product_lifecycle(session, event, state)
        return state

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
            )
            .values(
                status=product_status,
                error_code=failure_code,
                error_message=(
                    "Formal remediation execution terminated before a durable outcome was recorded"
                ),
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
            "ActivityProgressed",
            "ActivityCompleted",
            "ActivityFailed",
            "ActivityOutcomeUnknown",
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
                "ActivityProgressed": "running",
                "ActivityCompleted": "succeeded",
                "ActivityFailed": "failed",
                "ActivityOutcomeUnknown": "unknown",
            }[event.event_type]
            if event.event_type == "ActivityCallStarted":
                attempt += 1
        terminal = status in {"succeeded", "failed", "unknown"}
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
            await session.execute(
                update(ExecutionApprovalProjectionORM)
                .where(
                    ExecutionApprovalProjectionORM.approval_id == UUID(str(payload["approval_id"])),
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
            return
        if event.event_type in {"RunCompleted", "RunFailed", "RunCancelled"}:
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
            )

    async def _project_resource_build(
        self,
        session: AsyncSession,
        event: StoredEvent,
        state: RunState,
    ) -> None:
        if state.family not in {RunFamily.KB_INGEST, RunFamily.CODEBASE_INGEST}:
            return
        build_id = str(state.semantic_payload.get("build_id") or state.source_entity_id)
        resource_kind = "knowledge_base" if state.family == RunFamily.KB_INGEST else "codebase"
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
        phase = None
        if event.event_type == "ActivityProgressed":
            progress = int(event.public_payload["progress"])
            phase = self._optional_string(event.public_payload.get("phase"))
        elif existing is not None:
            phase = existing.phase
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
        if event.event_type in {"RunCancelled", "RunFailed"}:
            error_code = (
                "BUILD_CANCELLED"
                if event.event_type == "RunCancelled"
                else state.failure_code or "BUILD_FAILED"
            )
            if state.family == RunFamily.CODEBASE_INGEST:
                await session.execute(
                    update(CodebaseVersionORM)
                    .where(
                        CodebaseVersionORM.build_id == build_id,
                        CodebaseVersionORM.published_at.is_(None),
                    )
                    .values(state="failed")
                )
                await session.execute(
                    update(CodebaseModel)
                    .where(CodebaseModel.id == values["resource_id"])
                    .values(
                        status=case(
                            (CodebaseModel.active_version_id.is_not(None), "ready"),
                            else_="failed",
                        ),
                        error=error_code,
                        updated_at=event.occurred_at,
                    )
                )
            else:
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
                    .where(KnowledgeBaseModel.id == values["resource_id"])
                    .values(
                        status=case(
                            (
                                KnowledgeBaseModel.active_version_id.is_not(None),
                                "ready",
                            ),
                            else_="failed",
                        ),
                        error=error_code,
                        updated_at=event.occurred_at,
                    )
                )

    @staticmethod
    async def _project_public_event(
        session: AsyncSession,
        event: StoredEvent,
        state: RunState | None,
    ) -> None:
        public_shape = PostgresFormalProjector._public_shape(event)
        if public_shape is None:
            return
        event_type, payload = public_shape
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
            .on_conflict_do_nothing(index_elements=["position"])
        )

    @staticmethod
    def _public_shape(
        event: StoredEvent,
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
            return "approval", {
                **meta,
                "approval_id": str(payload["approval_id"]),
                "kind": "tool",
                "payload": {
                    "subject_activity_id": str(payload["subject_activity_id"]),
                    "tool_name": str(payload["subject_label"]),
                    "note": str(payload["risk_summary"]),
                },
                "options": ["approve", "reject"],
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
            if payload.get("activity_type") == "tool.call" and isinstance(public_data, dict):
                name = str(public_data.get("name") or "tool")
                return "tool", {
                    **meta,
                    "tool_call_id": str(public_data.get("tool_call_id") or ""),
                    "name": name,
                    "function": name,
                    "args": {},
                    "status": "started",
                }
            return None
        if event.event_type == "ActivityProgressed":
            return "resource_build", {
                **meta,
                "activity_id": str(payload["activity_id"]),
                "kind": str(payload["kind"]),
                "phase": payload.get("phase"),
                "status": payload.get("status"),
                "progress": int(payload["progress"]),
                "message": str(payload.get("message") or ""),
            }
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
                    "args": {},
                    "status": str(public_data.get("status") or "completed"),
                    "content": public_data.get("content"),
                }
            return None
        return None

    async def _checkpoint(
        self,
        session: AsyncSession,
        key: str,
    ) -> ExecutionProjectorCheckpointORM | None:
        checkpoint = await session.scalar(
            select(ExecutionProjectorCheckpointORM)
            .where(
                ExecutionProjectorCheckpointORM.projector_name == _CHECKPOINT_NAME,
                ExecutionProjectorCheckpointORM.owner_scope_key == key,
            )
            .with_for_update()
        )
        if checkpoint is not None:
            self._validate_checkpoint(checkpoint, key)
        return checkpoint

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
    async def _lock(session: AsyncSession, key: str) -> None:
        digest = hashlib.sha256(f"{_CHECKPOINT_NAME}\0{key}".encode()).digest()
        lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

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
    def _json_hash(value: dict) -> str:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None


__all__ = ["PostgresFormalProjector"]
