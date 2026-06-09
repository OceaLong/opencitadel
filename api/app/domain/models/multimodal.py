#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一多模态 content-part 类型与附件模型。"""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

# OpenAI-compatible content part types
CONTENT_TYPE_TEXT = "text"
CONTENT_TYPE_IMAGE_URL = "image_url"
CONTENT_TYPE_IMAGE_REF = "image_ref"
CONTENT_TYPE_AUDIO = "audio"
CONTENT_TYPE_VIDEO = "video"

IMAGE_PART_TYPES = frozenset({CONTENT_TYPE_IMAGE_URL, CONTENT_TYPE_IMAGE_REF})
MEDIA_PART_TYPES = frozenset({
    CONTENT_TYPE_IMAGE_URL,
    CONTENT_TYPE_IMAGE_REF,
    CONTENT_TYPE_AUDIO,
    CONTENT_TYPE_VIDEO,
})


class MediaAttachment(BaseModel):
    """通用多模态附件（图片 / 音频 / 视频帧）。"""
    mime_type: str = ""
    data_base64: str = ""
    ref_url: str = ""
    media_type: Literal["image", "audio", "video_frame"] = "image"
    duration_seconds: float = 0.0
    frame_index: int = 0
    transcript: str = ""


def is_media_part(part: Dict[str, Any]) -> bool:
    return isinstance(part, dict) and part.get("type") in MEDIA_PART_TYPES


def is_image_part(part: Dict[str, Any]) -> bool:
    return isinstance(part, dict) and part.get("type") in IMAGE_PART_TYPES


def build_text_part(text: str) -> Dict[str, Any]:
    return {"type": CONTENT_TYPE_TEXT, "text": text}


def build_audio_part(data_base64: str, mime_type: str = "audio/wav") -> Dict[str, Any]:
    return {"type": CONTENT_TYPE_AUDIO, "mime_type": mime_type, "data": data_base64}


def build_video_part(ref_url: str, mime_type: str = "video/mp4") -> Dict[str, Any]:
    return {"type": CONTENT_TYPE_VIDEO, "ref": ref_url, "mime_type": mime_type}
