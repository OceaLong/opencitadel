import asyncio
import logging
import re
from typing import Any

from app.domain.models.inference import InferenceCapabilities
from app.infrastructure.observability.llm_metrics import record_multimodal_fallback

logger = logging.getLogger(__name__)

_DATA_URL_PATTERN = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

_FALLBACK_IMAGE_NOTE = "原始消息包含图片附件，因模型服务连接异常已省略图片内容。"


def _guess_image_mime_from_url(url: str) -> str:
    lower = url.lower().split("?")[0]
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


def _has_multimodal_image_content(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}:
                return True
    return False


def _strip_multimodal_to_text(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback_messages: list[dict[str, Any]] = []
    for message in messages:
        cleaned = {k: v for k, v in message.items() if not k.startswith("_")}
        content = cleaned.get("content")
        if not isinstance(content, list):
            fallback_messages.append(cleaned)
            continue

        text_parts: list[str] = []
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

    _capabilities: InferenceCapabilities

    async def _apply_multimodal_fallback(
        self,
        error: Exception,
        request_kwargs: dict[str, Any],
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
    parts: list[dict[str, Any]] = []
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
                parts.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": data},
                    }
                )
            elif url.startswith(("http://", "https://")):
                parts.append(
                    {
                        "type": "image",
                        "source": {"type": "url", "url": url},
                    }
                )
    return parts or ""


def openai_content_to_gemini_parts(content: Any) -> list[dict[str, Any]]:
    """将 OpenAI 风格 content 转为 Gemini parts 列表。"""
    if isinstance(content, str):
        return [{"text": content}] if content else [{"text": ""}]
    if not isinstance(content, list):
        return [{"text": str(content) if content is not None else ""}]
    parts: list[dict[str, Any]] = []
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
            elif url.startswith(("http://", "https://")):
                mime_type = part.get("mime_type") or _guess_image_mime_from_url(url)
                parts.append({"fileData": {"mimeType": mime_type, "fileUri": url}})
    return parts or [{"text": ""}]


def normalize_usage(raw: dict[str, Any] | None, *, provider: str | None = None) -> dict[str, Any]:
    """Inclusive canonical totals plus nullable native categories (never add twice)."""
    raw = raw if isinstance(raw, dict) else {}

    def count(*path):
        value = raw
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value if type(value) is int and value >= 0 else None

    def add(*values):
        return None if any(v is None for v in values) else sum(values)

    provider = provider or (
        "gemini"
        if any(k.endswith("TokenCount") for k in raw)
        else "anthropic"
        if "input_tokens" in raw
        else "openai"
    )
    if provider == "anthropic":
        uncached = count("input_tokens")
        cached, written = count("cache_read_input_tokens"), count("cache_creation_input_tokens")
        # Optional omitted cache fields are known inapplicable to non-cache calls;
        # retain their absent native coverage separately.
        prompt = add(
            uncached,
            cached if "cache_read_input_tokens" in raw else 0,
            written if "cache_creation_input_tokens" in raw else 0,
        )
        completion = count("output_tokens")
        reasoning = None
        visible = None
        native_total = count("total_tokens")
    elif provider == "gemini":
        prompt, cached = count("promptTokenCount"), count("cachedContentTokenCount")
        written = None
        visible = (
            count("candidatesTokenCount")
            if "candidatesTokenCount" in raw
            else count("completionTokenCount")
        )
        reasoning = count("thoughtsTokenCount")
        completion = add(visible, reasoning if "thoughtsTokenCount" in raw else 0)
        native_total = count("totalTokenCount")
        uncached = (
            prompt - (cached or 0)
            if prompt is not None and (cached is None or cached <= prompt)
            else None
        )
    else:
        prompt, completion = count("prompt_tokens"), count("completion_tokens")
        cached = count("prompt_tokens_details", "cached_tokens")
        if cached is None and "prompt_cache_hit_tokens" in raw:
            cached = count("prompt_cache_hit_tokens")
        written = count("prompt_cache_miss_tokens")
        reasoning = count("completion_tokens_details", "reasoning_tokens")
        visible = (
            completion - reasoning
            if completion is not None and reasoning is not None and reasoning <= completion
            else None
        )
        uncached = (
            prompt - (cached or 0) - (written or 0)
            if prompt is not None and (cached or 0) + (written or 0) <= prompt
            else None
        )
        native_total = count("total_tokens")

    def category_state(*path):
        value = raw
        for key in path:
            if not isinstance(value, dict) or key not in value or value[key] is None:
                return "absent"
            value = value[key]
        return "reported" if type(value) is int and value >= 0 else "malformed"

    if provider == "anthropic":
        states = {
            "cached_tokens": category_state("cache_read_input_tokens"),
            "cache_write_tokens": category_state("cache_creation_input_tokens"),
            "reasoning_tokens": "inapplicable",
        }
    elif provider == "gemini":
        states = {
            "cached_tokens": category_state("cachedContentTokenCount"),
            "cache_write_tokens": "inapplicable",
            "reasoning_tokens": category_state("thoughtsTokenCount"),
        }
    else:
        states = {
            "cached_tokens": category_state("prompt_cache_hit_tokens")
            if "prompt_cache_hit_tokens" in raw
            else category_state("prompt_tokens_details", "cached_tokens"),
            "cache_write_tokens": category_state("prompt_cache_miss_tokens")
            if "prompt_cache_miss_tokens" in raw or "prompt_cache_hit_tokens" in raw
            else "inapplicable",
            "reasoning_tokens": category_state("completion_tokens_details", "reasoning_tokens"),
        }
    total = add(prompt, completion)
    return {
        "category_states": states,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "cache_write_tokens": written,
        "reasoning_tokens": reasoning,
        "uncached_tokens": uncached,
        "visible_output_tokens": visible,
        "native_total_tokens": native_total,
        "provider": provider,
        "cache_metric_source": "provider",
        "total_consistent": None
        if native_total is None or total is None
        else native_total == total,
    }
