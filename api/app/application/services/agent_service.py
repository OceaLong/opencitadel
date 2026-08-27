"""Session-facing admission/query facade for the universal execution Run."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.application.execution.admission import RunAdmissionService
from app.application.execution.command_ingress import CommandIngress
from app.application.execution.public_projection import (
    PublicEventPage,
    PublicExecutionEvent,
)
from app.application.ports.queries import PublicProjectionPort, RunProjectionPort
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.run import RunFamily
from app.domain.models.codebase import SessionMode
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork

_TERMINAL_EVENTS = frozenset({"done", "error"})


class AgentService:
    """Admit session Runs and expose only their sanitized event projection."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        admission_service: RunAdmissionService,
        command_ingress: CommandIngress,
        public_projection: PublicProjectionPort,
        run_projection: RunProjectionPort,
        poll_interval_seconds: float = 0.2,
        idle_timeout_seconds: float = 120.0,
    ) -> None:
        if poll_interval_seconds <= 0 or idle_timeout_seconds <= 0:
            raise ValueError("poll and idle timeouts must be positive")
        self._uow_factory = uow_factory
        self._admission = admission_service
        self._commands = command_ingress
        self._public = public_projection
        self._run_projection = run_projection
        self._poll_interval = poll_interval_seconds
        self._idle_timeout = idle_timeout_seconds

    async def chat(
        self,
        session_id: str,
        *,
        owner_scope: OwnerScope,
        message: str | None = None,
        request_id: UUID | None = None,
        attachments: list[str] | None = None,
        latest_event_id: str | None = None,
        timestamp: datetime | None = None,
        model_id: str | None = None,
        skill_id: str | None = None,
        thinking_enabled: bool | None = None,
        mode: SessionMode | None = None,
        **_: object,
    ) -> AsyncGenerator[PublicExecutionEvent, None]:
        if message is not None:
            message = message.strip()
            if not message:
                raise ValueError("message must not be blank")
        elif request_id is not None:
            raise ValueError("request_id is only valid when message is present")
        if message is not None and request_id is None:
            raise ValueError("message admission requires request_id")
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=owner_scope)
            effective_skill_id = _effective_id(skill_id, session.skill_id) if session else None
            skill = (
                await uow.skill.get_by_id(effective_skill_id, scope=owner_scope)
                if effective_skill_id
                else None
            )
            attachment_files = []
            for file_id in _attachment_ids(attachments):
                file = await uow.file.get_by_id(file_id, scope=owner_scope)
                if file is None:
                    raise ValueError(f"attachment does not exist or is not accessible: {file_id}")
                attachment_files.append(file)
        if session is None:
            raise ValueError("session does not exist")
        if effective_skill_id and (skill is None or not skill.enabled):
            raise ValueError("skill does not exist or is disabled")
        if attachment_files and not message:
            raise ValueError("attachments require a message")
        effective_model_id = _effective_id(model_id, session.model_id)
        if effective_model_id is None and skill is not None:
            effective_model_id = skill.recommended_model_id
        resolved_mode = mode or session.mode
        if attachment_files and resolved_mode == SessionMode.ASK:
            raise ValueError("temporary attachments require agent mode")
        private_attachments = [_private_attachment(file) for file in attachment_files]
        public_attachments = [_public_attachment(file) for file in attachment_files]
        resource_bindings = [
            {**binding.model_dump(mode="json"), "is_current": True}
            for binding in session.resource_bindings
        ]

        cursor = latest_event_id
        conversation: list[dict[str, str]] = []
        if cursor is None and message is not None:
            current = await self.list_events(
                session_id,
                owner_scope=owner_scope,
                latest=True,
                limit=1,
            )
            cursor = current.events[-1].cursor if current.events else None
        if message is not None:
            history = await self.list_events(
                session_id,
                owner_scope=owner_scope,
                latest=True,
                limit=100,
            )
            conversation = [
                {
                    "role": str(event.payload["role"]),
                    "content": str(event.payload["message"]),
                }
                for event in history.events
                if event.event_type == "message"
                and event.payload.get("role") in {"user", "assistant"}
                and isinstance(event.payload.get("message"), str)
            ]

        admitted_run_id: UUID | None = session.active_execution_run_id
        if message is not None:
            async with self._uow_factory() as uow:
                locked_session = await uow.session.lock_by_id(
                    session_id,
                    scope=owner_scope,
                )
                if locked_session is None:
                    raise ValueError("session does not exist")
                if locked_session.active_execution_run_id is not None:
                    if locked_session.active_execution_request_id != request_id:
                        raise ValueError("session already has an active Run")
                    admitted_run_id = locked_session.active_execution_run_id
                else:
                    if model_id is not None:
                        locked_session.model_id = model_id or None
                    if skill_id is not None:
                        locked_session.skill_id = skill_id or None
                    if thinking_enabled is not None:
                        locked_session.thinking_enabled = thinking_enabled
                    if mode is not None:
                        locked_session.mode = mode
                    locked_session.latest_message = message
                    locked_session.latest_message_at = timestamp or datetime.now(UTC)
                    for file in attachment_files:
                        await uow.session.add_file(session_id, file)
                    idempotency_key = f"session:{session_id}:request:{request_id}"
                    admitted_run_id = await self._admission.admit(
                        family=(
                            RunFamily.ASK if resolved_mode == SessionMode.ASK else RunFamily.AGENT
                        ),
                        source_entity_type="session",
                        source_entity_id=session_id,
                        owner_scope=owner_scope,
                        private_input={
                            "message": message,
                            "attachments": private_attachments,
                            "model_id": effective_model_id,
                            "skill_id": effective_skill_id,
                            "temperature_override": (
                                skill.agent_params.temperature_override
                                if skill is not None
                                else None
                            ),
                            "thinking_enabled": (
                                thinking_enabled
                                if thinking_enabled is not None
                                else session.thinking_enabled
                            ),
                            "session_id": session_id,
                            "mode": resolved_mode.value,
                            "operator_scope": session.operator_scope,
                            "operator_domains": list(session.operator_domains),
                            "conversation": conversation,
                            "resource_bindings": resource_bindings,
                        },
                        public_input={
                            "role": "user",
                            "message": message,
                            "attachments": public_attachments,
                            "resource_bindings": resource_bindings,
                        },
                        idempotency_key=idempotency_key,
                        command_sink=uow.execution_commands,
                    )
                    locked_session.active_execution_run_id = admitted_run_id
                    locked_session.active_execution_request_id = request_id
                    await uow.session.save(locked_session)
                await uow.commit()

        idle = 0.0
        while True:
            page = await self.list_events(
                session_id,
                owner_scope=owner_scope,
                run_id=admitted_run_id,
                after=cursor,
                limit=100,
            )
            if page.events:
                idle = 0.0
                for event in page.events:
                    cursor = event.cursor
                    yield event
                    if (
                        event.event_type in _TERMINAL_EVENTS
                        or (event.event_type == "approval" and bool(event.payload.get("options")))
                        or (
                            event.event_type == "session_status"
                            and event.payload.get("status") in {"waiting", "cancelled", "failed"}
                        )
                    ):
                        return
                continue
            await asyncio.sleep(self._poll_interval)
            idle += self._poll_interval
            if idle >= self._idle_timeout:
                return

    async def list_events(
        self,
        session_id: str,
        *,
        owner_scope: OwnerScope,
        run_id: UUID | None = None,
        after: str | None = None,
        before: str | None = None,
        latest: bool = False,
        limit: int = 100,
    ) -> PublicEventPage:
        return await self._public.list_events(
            source_entity_type="session",
            source_entity_id=session_id,
            owner_scope=owner_scope,
            run_id=run_id,
            after=after,
            before=before,
            latest=latest,
            limit=limit,
        )

    async def stop_session(
        self,
        session_id: str,
        *,
        owner_scope: OwnerScope,
    ) -> None:
        run_id = await self._run_projection.latest_active_run_id(
            source_entity_type="session",
            source_entity_id=session_id,
            owner_scope=owner_scope,
        )
        if run_id is None:
            async with self._uow_factory() as uow:
                session = await uow.session.get_by_id(
                    session_id,
                    scope=owner_scope,
                )
            run_id = session.active_execution_run_id if session else None
        if run_id is None:
            return
        owner_user_id, team_id = self._scope_parts(owner_scope)
        await self._commands.submit(
            RegisteredCommand(
                command_id=uuid5(
                    NAMESPACE_URL,
                    f"opencitadel:session-cancel:{run_id}",
                ),
                command_type="CancelRun",
                run_id=run_id,
                expected_stream_version=None,
                payload={"reason": "requested_by_user"},
            ),
            CommandContext(
                owner_user_id=owner_user_id,
                team_id=team_id,
                correlation_id=UUID(str(run_id)),
                causation_id=None,
                issued_at=datetime.now(UTC),
            ),
        )

    async def decide_approval(
        self,
        *,
        approval_id: UUID,
        owner_scope: OwnerScope,
        decision: str,
        actor_user_id: str,
        feedback: str = "",
    ) -> UUID:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision must be approved or rejected")
        run_id = await self._run_projection.run_id_for_pending_approval(
            approval_id=approval_id,
            owner_scope=owner_scope,
        )
        if run_id is None:
            raise ValueError("pending approval does not exist")
        owner_user_id, team_id = self._scope_parts(owner_scope)
        await self._commands.submit(
            RegisteredCommand(
                command_id=uuid5(
                    NAMESPACE_URL,
                    f"opencitadel:approval:{approval_id}:{decision}",
                ),
                command_type="DecideApproval",
                run_id=run_id,
                expected_stream_version=None,
                payload={
                    "approval_id": str(approval_id),
                    "decision": decision,
                    "actor_user_id": actor_user_id,
                    "feedback": feedback,
                },
            ),
            CommandContext(
                owner_user_id=owner_user_id,
                team_id=team_id,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
            ),
        )
        return run_id

    async def shutdown(self) -> None:
        """The API facade owns no background tasks or Redis resources."""

    @staticmethod
    def _scope_parts(owner_scope: OwnerScope) -> tuple[str | None, str | None]:
        if owner_scope.team_id is not None:
            return None, owner_scope.team_id
        return owner_scope.user_id, None


def _effective_id(requested: str | None, persisted: str | None) -> str | None:
    if requested == "":
        return None
    return requested if requested is not None else persisted


def _attachment_ids(attachments: list[str] | None) -> tuple[str, ...]:
    raw = attachments or []
    if len(raw) > 10:
        raise ValueError("at most 10 attachments are allowed")
    if any(not isinstance(file_id, str) or not file_id.strip() for file_id in raw):
        raise ValueError("attachment IDs must be non-empty strings")
    return tuple(dict.fromkeys(file_id.strip() for file_id in raw))


def _attachment_filename(file: File) -> str:
    basename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return f"{file.id}-{safe or 'attachment'}"


def _public_attachment(file: File) -> dict[str, object]:
    return {
        "file_id": file.id,
        "filename": file.filename,
        "mime_type": file.mime_type,
        "size": file.size,
    }


def _private_attachment(file: File) -> dict[str, object]:
    return {
        **_public_attachment(file),
        "sandbox_path": f"/home/ubuntu/uploads/{_attachment_filename(file)}",
    }


__all__ = ["AgentService"]
