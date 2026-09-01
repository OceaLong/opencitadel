"""A2A JSON-RPC facade over the formal execution event feed."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.application.ports.inference import CircuitBreakerPort
from app.application.ports.queries import RunProjectionPort
from app.application.services.agent_service import AgentService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.domain.errors import AppException
from app.domain.execution.run import RunStatus
from app.domain.models.scope import OwnerScope, Principal

logger = logging.getLogger(__name__)
A2A_MODEL_UNAVAILABLE_CODE = -32001
# JSON-RPC / A2A protocol error codes (see the A2A specification).
A2A_INVALID_PARAMS_CODE = -32602
A2A_TASK_NOT_FOUND_CODE = -32001

# Map the internal execution Run lifecycle onto the A2A task-state vocabulary.
# A2A states: submitted / working / input-required / completed / canceled /
# failed / rejected / auth-required / unknown.
RUN_STATUS_TO_A2A_STATE: dict[RunStatus, str] = {
    RunStatus.NEW: "submitted",
    RunStatus.QUEUED: "submitted",
    RunStatus.RUNNING: "working",
    # A Run waits on either an approval or a retry back-off; from the caller's
    # perspective the actionable case is an approval that needs their input.
    RunStatus.WAITING: "input-required",
    RunStatus.COMPLETED: "completed",
    RunStatus.FAILED: "failed",
    RunStatus.CANCELLED: "canceled",
}


def extract_text_from_a2a_params(params: dict[str, Any]) -> str:
    parts = (params.get("message") or {}).get("parts") or []
    return "\n".join(
        text.strip()
        for part in parts
        if isinstance(part, dict) and isinstance((text := part.get("text")), str) and text.strip()
    )


def build_a2a_text_response(request_id: Any, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def build_a2a_error_response(
    request_id: Any,
    message: str,
    code: int = -32000,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def build_a2a_task_response(
    request_id: Any,
    task_id: str,
    state: str,
    *,
    context_id: str | None = None,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "id": task_id,
            "contextId": context_id or task_id,
            "kind": "task",
            "status": {
                "state": state,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        },
    }


class A2AServerService:
    def __init__(
        self,
        agent_service: AgentService,
        session_service: SessionService,
        skill_service: SkillService,
        inference_model_service: InferenceModelService,
        policy_heads: PolicyHeadReader,
        breaker: CircuitBreakerPort,
        run_projection: RunProjectionPort | None = None,
    ) -> None:
        self._agent_service = agent_service
        self._session_service = session_service
        self._skill_service = skill_service
        self._inference_model_service = inference_model_service
        self._policy_heads = policy_heads
        self._breaker = breaker
        self._run_projection = run_projection

    async def build_agent_card(self, base_url: str) -> dict[str, Any]:
        # The agent-card is an unauthenticated public discovery endpoint, so it
        # must only expose globally-visible Skills — never tenant-private/team
        # Skills that would leak across tenants.
        skills = await self._skill_service.list_skills(enabled_only=True, global_only=True)
        return {
            "name": "OpenCitadel",
            "description": "Event-sourced AI execution agent",
            "url": f"{base_url.rstrip('/')}/api/a2a",
            "version": "2.0.0",
            "capabilities": {"streaming": True},
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description or skill.slug,
                }
                for skill in skills
            ],
        }

    async def _precheck_model(self, scope: OwnerScope) -> dict[str, Any] | None:
        try:
            model = await self._inference_model_service.resolve_chat(scope=scope)
        except AppException:
            return build_a2a_error_response(
                None,
                "The chat inference binding is not configured",
                A2A_MODEL_UNAVAILABLE_CODE,
            )
        active = await self._policy_heads.active_execution(
            require_fresh=True,
            now=datetime.now(UTC),
        )
        if await self._breaker.is_open(
            model.id,
            active.revision.policy.model_resilience,
        ):
            return build_a2a_error_response(
                None,
                "The model provider is temporarily unavailable",
                A2A_MODEL_UNAVAILABLE_CODE,
            )
        return None

    async def handle_message_send(
        self,
        payload: dict[str, Any],
        *,
        principal: Principal,
    ) -> dict[str, Any]:
        request_id = payload.get("id")
        query = extract_text_from_a2a_params(payload.get("params") or {})
        if not query:
            return build_a2a_error_response(request_id, "message.parts requires text")
        scope = OwnerScope.personal(principal.user_id)
        guard = await self._precheck_model(scope)
        if guard:
            guard["id"] = request_id
            return guard

        session = await self._session_service.create_session(
            title="A2A Request",
            scope=scope,
        )
        final_text = ""
        try:
            async for event in self._agent_service.chat(
                session.id,
                owner_scope=scope,
                message=query,
                request_id=uuid.uuid4(),
            ):
                if event.event_type == "message" and event.payload.get("role") == "assistant":
                    message = event.payload.get("message")
                    if isinstance(message, str):
                        final_text = message
                elif event.event_type == "error":
                    code = str(event.payload.get("code") or "RUN_FAILED")
                    return build_a2a_error_response(request_id, code)
        except (OSError, RuntimeError, ValueError) as error:
            logger.exception("A2A message/send failed")
            return build_a2a_error_response(request_id, str(error))
        return build_a2a_text_response(
            request_id,
            final_text or "The Run completed without a text result.",
        )

    async def stream_message_events(
        self,
        payload: dict[str, Any],
        *,
        principal: Principal,
    ):
        request_id = payload.get("id")
        query = extract_text_from_a2a_params(payload.get("params") or {})
        if not query:
            yield json.dumps(build_a2a_error_response(request_id, "message.parts requires text"))
            return
        scope = OwnerScope.personal(principal.user_id)
        guard = await self._precheck_model(scope)
        if guard:
            guard["id"] = request_id
            yield json.dumps(guard)
            return

        session = await self._session_service.create_session(
            title="A2A Stream",
            scope=scope,
        )
        accumulated = ""
        try:
            async for event in self._agent_service.chat(
                session.id,
                owner_scope=scope,
                message=query,
            ):
                if event.event_type == "message" and event.payload.get("role") == "assistant":
                    message = event.payload.get("message")
                    if isinstance(message, str) and message:
                        accumulated = message
                        yield json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": {"kind": "text", "text": message},
                            },
                            ensure_ascii=False,
                        )
                elif event.event_type == "error":
                    code = str(event.payload.get("code") or "RUN_FAILED")
                    yield json.dumps(build_a2a_error_response(request_id, code))
                    return
            yield json.dumps(
                build_a2a_text_response(
                    request_id,
                    accumulated or "The Run completed without a text result.",
                ),
                ensure_ascii=False,
            )
        except (OSError, RuntimeError, ValueError) as error:
            logger.exception("A2A message/stream failed")
            yield json.dumps(build_a2a_error_response(request_id, str(error)))

    @staticmethod
    def _task_id_from_params(payload: dict[str, Any]) -> str | None:
        task_id = (payload.get("params") or {}).get("id")
        return task_id if isinstance(task_id, str) and task_id else None

    async def _resolve_task_state(
        self,
        task_id: str,
        session: Any,
        scope: OwnerScope,
    ) -> str:
        # An A2A task maps to the session's execution Run. Prefer the live
        # projection; fall back to the run id the session still pins so a task
        # that has already terminated is still reportable.
        run_id = None
        if self._run_projection is not None:
            run_id = await self._run_projection.latest_active_run_id(
                source_entity_type="session",
                source_entity_id=task_id,
                owner_scope=scope,
            )
        if run_id is None:
            run_id = getattr(session, "active_execution_run_id", None)
        if run_id is None or self._run_projection is None:
            # No Run has been admitted for this session yet.
            return "submitted"
        status = await self._run_projection.status_for_run(
            run_id=run_id,
            owner_scope=scope,
        )
        if status is None:
            return "submitted"
        return RUN_STATUS_TO_A2A_STATE.get(status, "unknown")

    async def handle_task_get(
        self,
        payload: dict[str, Any],
        *,
        principal: Principal,
    ) -> dict[str, Any]:
        request_id = payload.get("id")
        task_id = self._task_id_from_params(payload)
        if task_id is None:
            return build_a2a_error_response(
                request_id,
                "params.id is required",
                A2A_INVALID_PARAMS_CODE,
            )
        scope = OwnerScope.personal(principal.user_id)
        session = await self._session_service.get_session(task_id, scope=scope)
        if not session:
            return build_a2a_error_response(
                request_id,
                "Task not found",
                A2A_TASK_NOT_FOUND_CODE,
            )
        state = await self._resolve_task_state(task_id, session, scope)
        return build_a2a_task_response(request_id, task_id, state)

    async def handle_task_cancel(
        self,
        payload: dict[str, Any],
        *,
        principal: Principal,
    ) -> dict[str, Any]:
        request_id = payload.get("id")
        task_id = self._task_id_from_params(payload)
        if task_id is None:
            return build_a2a_error_response(
                request_id,
                "params.id is required",
                A2A_INVALID_PARAMS_CODE,
            )
        scope = OwnerScope.personal(principal.user_id)
        session = await self._session_service.get_session(task_id, scope=scope)
        if not session:
            return build_a2a_error_response(
                request_id,
                "Task not found",
                A2A_TASK_NOT_FOUND_CODE,
            )
        # Reuse the existing session-stop path; it submits a CancelRun command
        # and is a no-op when no Run is active.
        await self._agent_service.stop_session(task_id, owner_scope=scope)
        return build_a2a_task_response(request_id, task_id, "canceled")


__all__ = [
    "RUN_STATUS_TO_A2A_STATE",
    "A2AServerService",
    "build_a2a_error_response",
    "build_a2a_task_response",
    "build_a2a_text_response",
    "extract_text_from_a2a_params",
]
