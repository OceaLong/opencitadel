#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from app.domain.models.llm_model import ModelCapabilities
from app.infrastructure.observability.llm_metrics import record_multimodal_fallback

logger = logging.getLogger(__name__)

_DATA_URL_PATTERN = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

_FALLBACK_IMAGE_NOTE = "原始消息包含图片附件，因模型服务连接异常已省略图片内容。"


def _guess_image_mime_from_url(url: str) -> str:
    lower = url.lower().split("?")[0]
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


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

            compressed_messages = await asyncio.to_thread(
                compress_messages_for_retry,
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


def parse_data_url(url: str) -> tuple[str, str]:
    """解析 data URL，返回 (mime_type, base64_data)。"""
    match = _DATA_URL_PATTERN.match(url.strip())
    if not match:
        raise ValueError(f"无效的 data URL: {url[:80]}")
    return match.group(1), match.group(2)


def openai_content_to_anthropic_parts(content: Any) -> Any:
    """将 OpenAI 风格 user/assistant content 转为 Anthropic content blocks。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content if content is not None else ""
    parts: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                parts.append({"type": "text", "text": str(text)})
        elif part_type == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if not url:
                continue
            if url.startswith("data:"):
                mime_type, data = parse_data_url(url)
                parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime_type, "data": data},
                })
            elif url.startswith("http://") or url.startswith("https://"):
                parts.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
    return parts if parts else ""


def openai_content_to_gemini_parts(content: Any) -> List[Dict[str, Any]]:
    """将 OpenAI 风格 content 转为 Gemini parts 列表。"""
    if isinstance(content, str):
        return [{"text": content}] if content else [{"text": ""}]
    if not isinstance(content, list):
        return [{"text": str(content) if content is not None else ""}]
    parts: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                parts.append({"text": str(text)})
        elif part_type == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if not url:
                continue
            if url.startswith("data:"):
                mime_type, data = parse_data_url(url)
                parts.append({"inlineData": {"mimeType": mime_type, "data": data}})
            elif url.startswith("http://") or url.startswith("https://"):
                mime_type = part.get("mime_type") or _guess_image_mime_from_url(url)
                parts.append({"fileData": {"mimeType": mime_type, "fileUri": url}})
    return parts if parts else [{"text": ""}]


def _int_from_path(raw: Dict[str, Any], *path: str) -> int:
    value: Any = raw
    for key in path:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(raw: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """统一 usage 字段为 prompt/completion/total/cache tokens。"""
    if not raw:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
        }
    prompt = int(
        raw.get("prompt_tokens")
        or raw.get("input_tokens")
        or raw.get("promptTokenCount")
        or 0
    )
    completion = int(
        raw.get("completion_tokens")
        or raw.get("output_tokens")
        or raw.get("completionTokenCount")
        or raw.get("candidatesTokenCount")
        or 0
    )
    total = int(raw.get("total_tokens") or raw.get("totalTokenCount") or (prompt + completion))
    cached = int(
        _int_from_path(raw, "prompt_tokens_details", "cached_tokens")
        or raw.get("prompt_cache_hit_tokens")
        or raw.get("cache_read_input_tokens")
        or raw.get("cachedContentTokenCount")
        or 0
    )
    cache_write = int(
        raw.get("prompt_cache_miss_tokens")
        or raw.get("cache_creation_input_tokens")
        or 0
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
    }


async def invoke_to_stream_deltas(message: Dict[str, Any]):
    """Convert a complete LLM message into stream deltas (fallback for non-native streaming)."""
    content = message.get("content")
    if content:
        yield {"content": content}
    reasoning = message.get("reasoning_content")
    if reasoning:
        yield {"reasoning_content": reasoning}
    for idx, tool_call in enumerate(message.get("tool_calls") or []):
        fn = tool_call.get("function") or {}
        yield {
            "tool_calls": [{
                "index": idx,
                "id": tool_call.get("id"),
                "function": {
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "",
                },
            }]
        }
    usage = message.get("_usage")
    if usage:
        yield {"usage": normalize_usage(usage)}
