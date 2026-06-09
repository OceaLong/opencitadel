#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Expose MyManus as an A2A-compatible agent server."""
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent, WaitEvent

logger = logging.getLogger(__name__)


def extract_text_from_a2a_params(params: Dict[str, Any]) -> str:
    """从 A2A message/send 或 message/stream 的 params 中提取用户文本。"""
    message = params.get("message") or {}
    parts = message.get("parts") or []
    texts: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts).strip()


def build_a2a_text_response(request_id: Any, text: str) -> Dict[str, Any]:
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
    """将 MyManus 会话能力适配为 A2A JSON-RPC 接口。"""

    def __init__(
            self,
            agent_service: AgentService,
            session_service: SessionService,
            skill_service: SkillService,
    ) -> None:
        self._agent_service = agent_service
        self._session_service = session_service
        self._skill_service = skill_service

    async def build_agent_card(self, base_url: str) -> Dict[str, Any]:
        base = base_url.rstrip("/")
        skills = await self._skill_service.list_skills(enabled_only=True)
        return {
            "name": "MyManus",
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

    async def handle_message_send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = payload.get("id")
        params = payload.get("params") or {}
        query = extract_text_from_a2a_params(params)
        if not query:
            return build_a2a_error_response(request_id, "message.parts 中缺少 text 内容")

        session = await self._session_service.create_session(title="A2A Request")
        final_text = ""
        try:
            async for event in self._agent_service.chat(session.id, message=query):
                if isinstance(event, MessageEvent) and event.role == "assistant" and event.message:
                    final_text = event.message
                elif isinstance(event, ErrorEvent):
                    return build_a2a_error_response(request_id, event.error or "Agent 执行失败")
                elif isinstance(event, WaitEvent):
                    return build_a2a_error_response(
                        request_id,
                        "Agent 正在等待用户输入，A2A 同步调用暂不支持人机交互等待",
                    )
                elif isinstance(event, DoneEvent):
                    break
        except Exception as exc:
            logger.exception("A2A message/send 失败: %s", exc)
            return build_a2a_error_response(request_id, str(exc))

        if not final_text:
            final_text = "任务已完成，但未产生可展示的文本回复。"
        return build_a2a_text_response(request_id, final_text)

    async def stream_message_events(self, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        request_id = payload.get("id")
        params = payload.get("params") or {}
        query = extract_text_from_a2a_params(params)
        if not query:
            yield json.dumps(build_a2a_error_response(request_id, "message.parts 中缺少 text 内容"))
            return

        session = await self._session_service.create_session(title="A2A Stream")
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
                    yield json.dumps(build_a2a_error_response(request_id, event.error or "Agent 执行失败"))
                    return
                elif isinstance(event, WaitEvent):
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
            yield json.dumps(build_a2a_error_response(request_id, str(exc)))
