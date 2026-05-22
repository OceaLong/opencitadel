#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, List, Optional

from app.domain.models.llm_model import ModelCapabilities
from app.infrastructure.observability.llm_metrics import record_multimodal_fallback

logger = logging.getLogger(__name__)

_FALLBACK_IMAGE_NOTE = "原始消息包含图片附件，因模型服务连接异常已省略图片内容。"


def _has_multimodal_image_content(messages: List[Dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}:
                return True
    return False


def _strip_multimodal_to_text(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fallback_messages: List[Dict[str, Any]] = []
    for message in messages:
        cleaned = {k: v for k, v in message.items() if not k.startswith("_")}
        content = cleaned.get("content")
        if not isinstance(content, list):
            fallback_messages.append(cleaned)
            continue

        text_parts: List[str] = []
        had_image = False
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text")
                if text:
                    text_parts.append(str(text))
            elif part.get("type") in {"image_url", "image_ref"}:
                had_image = True

        if text_parts:
            cleaned["content"] = "\n".join(text_parts)
        elif had_image:
            cleaned["content"] = _FALLBACK_IMAGE_NOTE
        else:
            cleaned["content"] = ""
        fallback_messages.append(cleaned)
    return fallback_messages


def classify_multimodal_error(error: Exception) -> str:
    status_code = getattr(error, "status_code", None)
    if status_code in {400, 415}:
        return "invalid_image"
    if status_code == 413:
        return "payload_too_large"
    error_text = str(error).lower()
    if "timeout" in error_text or "timed out" in error_text:
        return "timeout"
    if "connection error" in error_text or "connecterror" in error_text:
        return "connection"
    error_type = type(error).__name__.lower()
    if "connection" in error_type:
        return "connection"
    if "timeout" in error_type:
        return "timeout"
    return "unknown"


def is_retriable_multimodal_error(error: Exception) -> bool:
    reason = classify_multimodal_error(error)
    return reason in {"invalid_image", "payload_too_large", "connection", "timeout"}


class MultimodalFallbackMixin:
    """OpenAI-compatible LLM 多模态失败降级 mixin。"""

    _capabilities: ModelCapabilities

    async def _apply_multimodal_fallback(
            self,
            error: Exception,
            request_kwargs: Dict[str, Any],
            create_fn,
    ) -> Any:
        messages = request_kwargs.get("messages") or []
        if not _has_multimodal_image_content(messages):
            raise error

        reason = classify_multimodal_error(error)
        record_multimodal_fallback(reason)

        if reason == "payload_too_large":
            from app.domain.services.vision_service import compress_messages_for_retry

            compressed_messages = compress_messages_for_retry(
                messages,
                self._capabilities.max_image_bytes,
            )
            logger.warning("多模态请求 payload 过大，压缩图片后重试: error=%s", error)
            retry_kwargs = {**request_kwargs, "messages": compressed_messages}
            return await create_fn(retry_kwargs)

        fallback_messages = _strip_multimodal_to_text(messages)
        logger.warning("多模态请求失败，降级为文本请求重试: reason=%s error=%s", reason, error)
        retry_kwargs = {**request_kwargs, "messages": fallback_messages}
        return await create_fn(retry_kwargs)
