#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A2A inbound guard: reject before session creation when model circuit is open."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.application.services.agent_service import AgentService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.config_provider import get_runtime_config
from app.domain.models.error_codes import MODEL_NOT_CONFIGURED, MODEL_UNAVAILABLE
from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent, WaitEvent
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.session import SessionStatus
from app.infrastructure.external.llm.circuit_breaker import get_llm_circuit_breaker
from app.infrastructure.storage.postgres import get_uow

logger = logging.getLogger(__name__)

A2A_MODEL_UNAVAILABLE_CODE = -32001


def extract_text_from_a2a_params(params: Dict[str, Any]) -> str:
    """从 A2A message/send 或 message/stream 的 params 中提取用户文本。"""
    message = params.get("message") or {}
    parts = message.get("parts") or []
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts).strip()


def build_a2a_text_response(request_id: Any, text: str) -> Dict[str, Any]:
    import uuid

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": text}],
            },
        },
    }


def build_a2a_error_response(request_id: Any, message: str, code: int = -32000) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class A2AServerService:
    """将 OpenCitadel 会话能力适配为 A2A JSON-RPC 接口。"""

    def __init__(
            self,
            agent_service: AgentService,
            session_service: SessionService,
            skill_service: SkillService,
            llm_model_service: LLMModelService,
    ) -> None:
        self._agent_service = agent_service
        self._session_service = session_service
        self._skill_service = skill_service
        self._llm_model_service = llm_model_service
        self._breaker = get_llm_circuit_breaker()

    async def build_agent_card(self, base_url: str) -> Dict[str, Any]:
        base = base_url.rstrip("/")
        skills = await self._skill_service.list_skills(enabled_only=True)
        return {
            "name": "OpenCitadel",
            "description": "通用 AI Agent 系统，支持规划、工具调用、MCP 与沙箱执行",
            "url": f"{base}/api/a2a",
            "version": "1.0.0",
            "capabilities": {
                "streaming": True,
            },
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description or skill.slug,
                }
                for skill in skills
            ],
        }

    async def _precheck_model(self) -> Optional[Dict[str, Any]]:
        runtime = get_runtime_config()
        if not runtime.feature_flags.enable_agent_features:
            return build_a2a_error_response(None, "Agent 功能已关闭", A2A_MODEL_UNAVAILABLE_CODE)
        default_model = await self._llm_model_service.get_default_model()
        if not default_model:
            return build_a2a_error_response(
                None,
                "未配置默认模型，请先在设置中添加模型",
                A2A_MODEL_UNAVAILABLE_CODE,
            )
        if runtime.model_resilience.fast_fail_on_open_circuit and await self._breaker.is_open(default_model.id):
            return build_a2a_error_response(
                None,
                "模型服务暂不可用（熔断开路），请稍后重试",
                A2A_MODEL_UNAVAILABLE_CODE,
            )
        return None

    async def _mark_session_failed(self, session_id: str, error_code: str, message: str) -> None:
        async with get_uow() as uow:
            await uow.session.update_status(session_id, SessionStatus.FAILED)
        logger.info(
            "A2A 会话轻量废弃: session_id=%s error_code=%s message=%s",
            session_id,
            error_code,
            message,
        )

    async def handle_message_send(self, payload: Dict[str, Any], *, principal: Principal) -> Dict[str, Any]:
        request_id = payload.get("id")
        params = payload.get("params") or {}
        query = extract_text_from_a2a_params(params)
        if not query:
            return build_a2a_error_response(request_id, "message.parts 中缺少 text 内容")

        guard = await self._precheck_model()
        if guard:
            guard["id"] = request_id
            return guard

        session = await self._session_service.create_session(
            title="A2A Request",
            scope=OwnerScope.personal(principal.user_id),
        )
        final_text = ""
        try:
            async for event in self._agent_service.chat(session.id, message=query):
                if isinstance(event, MessageEvent) and event.role == "assistant" and event.message:
                    final_text = event.message
                elif isinstance(event, ErrorEvent):
                    code = event.code or MODEL_UNAVAILABLE
                    await self._mark_session_failed(session.id, code, event.error or "Agent 执行失败")
                    return build_a2a_error_response(
                        request_id,
                        event.error or "Agent 执行失败",
                        A2A_MODEL_UNAVAILABLE_CODE if code.startswith("MODEL_") else -32000,
                    )
                elif isinstance(event, WaitEvent):
                    await self._mark_session_failed(session.id, MODEL_UNAVAILABLE, "等待用户输入")
                    return build_a2a_error_response(
                        request_id,
                        "Agent 正在等待用户输入，A2A 同步调用暂不支持人机交互等待",
                    )
                elif isinstance(event, DoneEvent):
                    break
        except Exception as exc:
            logger.exception("A2A message/send 失败: %s", exc)
            await self._mark_session_failed(session.id, MODEL_UNAVAILABLE, str(exc))
            return build_a2a_error_response(request_id, str(exc))

        if not final_text:
            final_text = "任务已完成，但未产生可展示的文本回复。"
        return build_a2a_text_response(request_id, final_text)

    async def stream_message_events(self, payload: Dict[str, Any], *, principal: Principal):
        import json
        import uuid

        request_id = payload.get("id")
        params = payload.get("params") or {}
        query = extract_text_from_a2a_params(params)
        if not query:
            yield json.dumps(build_a2a_error_response(request_id, "message.parts 中缺少 text 内容"))
            return

        guard = await self._precheck_model()
        if guard:
            guard["id"] = request_id
            yield json.dumps(guard)
            return

        session = await self._session_service.create_session(
            title="A2A Stream",
            scope=OwnerScope.personal(principal.user_id),
        )
        accumulated = ""
        try:
            async for event in self._agent_service.chat(session.id, message=query):
                if isinstance(event, MessageEvent) and event.role == "assistant" and event.message:
                    delta = event.message[len(accumulated):] if event.message.startswith(accumulated) else event.message
                    accumulated = event.message
                    if delta:
                        chunk = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "kind": "text",
                                "text": delta,
                            },
                        }
                        yield json.dumps(chunk, ensure_ascii=False)
                elif isinstance(event, ErrorEvent):
                    await self._mark_session_failed(session.id, event.code or MODEL_UNAVAILABLE, event.error or "")
                    yield json.dumps(build_a2a_error_response(request_id, event.error or "Agent 执行失败"))
                    return
                elif isinstance(event, WaitEvent):
                    await self._mark_session_failed(session.id, MODEL_UNAVAILABLE, "等待用户输入")
                    yield json.dumps(build_a2a_error_response(
                        request_id,
                        "Agent 正在等待用户输入，A2A 流式调用暂不支持人机交互等待",
                    ))
                    return
                elif isinstance(event, DoneEvent):
                    break

            yield json.dumps(build_a2a_text_response(request_id, accumulated or "任务已完成。"), ensure_ascii=False)
        except Exception as exc:
            logger.exception("A2A message/stream 失败: %s", exc)
            await self._mark_session_failed(session.id, MODEL_UNAVAILABLE, str(exc))
            yield json.dumps(build_a2a_error_response(request_id, str(exc)))
