#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 消息归一化工具函数。

从 ``BaseAgent`` 中抽出的纯函数集合：把内部消息表示（含 reasoning_content、
image_ref 等内部字段）转换为发送给 LLM provider 前的安全形态。原本是
``BaseAgent`` 上的 4 个 ``@staticmethod``，行为完全保持，仅搬迁为模块级函数。
"""
import logging
from typing import Any, Dict, List, Optional

from app.domain.external.llm import LLM
from app.domain.models.message import Message
from app.domain.services import vision_service

logger = logging.getLogger(__name__)


def coerce_user_content(content: Any) -> Any:
    """Coerce mistaken Message/dict payloads into provider-safe user content."""
    if isinstance(content, (str, list)) or content is None:
        return content
    if isinstance(content, Message):
        return content.message
    if isinstance(content, dict) and "message" in content:
        msg = content.get("message")
        return msg if isinstance(msg, str) else str(msg)
    return str(content)


def normalize_message_for_llm(message: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure provider-safe message shape (no null content, reasoning fallback)."""
    normalized = dict(message)
    role = normalized.get("role")
    content = normalized.get("content")
    reasoning = normalized.get("reasoning_content")
    tool_calls = normalized.get("tool_calls")

    if role == "assistant":
        if (content is None or (isinstance(content, str) and not content.strip())) and reasoning:
            if not tool_calls:
                normalized["content"] = reasoning if isinstance(reasoning, str) else str(reasoning)
        if normalized.get("content") is None:
            normalized["content"] = ""
        if tool_calls and isinstance(normalized.get("content"), str) and not normalized["content"]:
            normalized["content"] = ""
    elif role in {"user", "system"} and "content" in normalized:
        if normalized["content"] is None:
            normalized["content"] = ""
        elif not isinstance(normalized["content"], (str, list)):
            normalized["content"] = coerce_user_content(normalized["content"])

    if role != "assistant" and "reasoning_content" in normalized:
        del normalized["reasoning_content"]

    return normalized


def messages_for_llm(
        messages: List[Dict[str, Any]],
        llm: Optional[LLM] = None,
        *,
        strip_images: bool = False,
) -> List[Dict[str, Any]]:
    """发送给 LLM 前移除内部字段，并将 image_ref 还原为 provider 可识别格式。"""
    sanitized: List[Dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            sanitized.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id"),
                "content": message.get("content"),
            })
        else:
            cleaned = {k: v for k, v in message.items() if not k.startswith("_")}
            sanitized.append(normalize_message_for_llm(cleaned))
    inflated = vision_service.inflate_messages_for_llm(sanitized, llm)
    if strip_images or (llm is not None and not vision_service.vision_enabled(llm)):
        return vision_service.strip_images_for_tool_call(inflated)
    return inflated


def assistant_message_from_llm_response(
        *,
        content: Optional[str],
        reasoning_content: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]],
        stream_id: str,
) -> Dict[str, Any]:
    """Build assistant memory entry; never persist content=null."""
    effective_content = content.strip() if isinstance(content, str) and content.strip() else ""
    effective_reasoning = (
        reasoning_content.strip()
        if isinstance(reasoning_content, str) and reasoning_content.strip()
        else ""
    )
    if not effective_content and not tool_calls and effective_reasoning:
        logger.warning(
            "LLM仅返回reasoning_content，回退为content写入记忆"
        )
        effective_content = effective_reasoning

    filtered_message: Dict[str, Any] = {
        "role": "assistant",
        "content": effective_content,
    }
    if effective_reasoning and effective_reasoning != effective_content:
        filtered_message["reasoning_content"] = effective_reasoning
    if tool_calls:
        filtered_message["tool_calls"] = tool_calls
        filtered_message["stream_id"] = stream_id
    return filtered_message
