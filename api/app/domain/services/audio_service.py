#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""音频转写服务（OpenAI-compatible whisper / 原生 audio API）。"""
import base64
import logging
from typing import Optional

import httpx

from app.domain.models.llm_model import LLMModel

logger = logging.getLogger(__name__)


async def transcribe_audio_bytes(
        audio_bytes: bytes,
        mime_type: str,
        model: LLMModel,
        *,
        language: Optional[str] = None,
) -> str:
    """将音频字节转写为文本。优先使用 OpenAI Whisper API。"""
    if model.provider.value not in {"openai", "azure"}:
        # 非 OpenAI provider：尝试 OpenAI-compatible whisper endpoint
        pass

    base_url = str(model.base_url).rstrip("/")
    if base_url.endswith("/v1"):
        whisper_url = f"{base_url}/audio/transcriptions"
    else:
        whisper_url = f"{base_url}/v1/audio/transcriptions"

    ext = "wav"
    if "mpeg" in mime_type or "mp3" in mime_type:
        ext = "mp3"
    elif "ogg" in mime_type:
        ext = "ogg"
    elif "webm" in mime_type:
        ext = "webm"

    files = {"file": (f"audio.{ext}", audio_bytes, mime_type)}
    data = {"model": "whisper-1"}
    if language:
        data["language"] = language

    headers = {"Authorization": f"Bearer {model.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(whisper_url, files=files, data=data, headers=headers)
            response.raise_for_status()
            payload = response.json()
            return payload.get("text", "")
    except Exception as exc:
        logger.warning("Whisper 转写失败: %s", exc)
        return ""


async def transcribe_attachment_base64(
        data_base64: str,
        mime_type: str,
        model: LLMModel,
) -> str:
    audio_bytes = base64.b64decode(data_base64, validate=False)
    return await transcribe_audio_bytes(audio_bytes, mime_type, model)
