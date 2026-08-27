"""A2A JSON-RPC facade over the formal execution event feed."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.application.ports.inference import CircuitBreakerPort
from app.application.services.agent_service import AgentService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.domain.errors import AppException
from app.domain.models.scope import OwnerScope, Principal

logger = logging.getLogger(__name__)
A2A_MODEL_UNAVAILABLE_CODE = -32001


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


class A2AServerService:
    def __init__(
        self,
        agent_service: AgentService,
        session_service: SessionService,
        skill_service: SkillService,
        inference_model_service: InferenceModelService,
        policy_heads: PolicyHeadReader,
        breaker: CircuitBreakerPort,
    ) -> None:
        self._agent_service = agent_service
        self._session_service = session_service
        self._skill_service = skill_service
        self._inference_model_service = inference_model_service
        self._policy_heads = policy_heads
        self._breaker = breaker

    async def build_agent_card(self, base_url: str) -> dict[str, Any]:
        skills = await self._skill_service.list_skills(enabled_only=True)
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


__all__ = [
    "A2AServerService",
    "build_a2a_error_response",
    "build_a2a_text_response",
    "extract_text_from_a2a_params",
]
