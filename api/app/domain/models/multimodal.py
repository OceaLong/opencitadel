"""统一多模态 content-part 类型与附件模型。"""

from typing import Any, Literal

from pydantic import BaseModel

# OpenAI-compatible content part types
CONTENT_TYPE_TEXT = "text"
CONTENT_TYPE_IMAGE_URL = "image_url"
CONTENT_TYPE_IMAGE_REF = "image_ref"
CONTENT_TYPE_AUDIO = "audio"

IMAGE_PART_TYPES = frozenset({CONTENT_TYPE_IMAGE_URL, CONTENT_TYPE_IMAGE_REF})


class MediaAttachment(BaseModel):
    """通用多模态附件（图片 / 音频 / 视频帧）。"""

    mime_type: str = ""
    data_base64: str = ""
    ref_url: str = ""
    media_type: Literal["image", "audio", "video_frame"] = "image"
    duration_seconds: float = 0.0
    frame_index: int = 0
    transcript: str = ""


def is_image_part(part: dict[str, Any]) -> bool:
    return isinstance(part, dict) and part.get("type") in IMAGE_PART_TYPES


def build_text_part(text: str) -> dict[str, Any]:
    return {"type": CONTENT_TYPE_TEXT, "text": text}


def build_audio_part(data_base64: str, mime_type: str = "audio/wav") -> dict[str, Any]:
    return {"type": CONTENT_TYPE_AUDIO, "mime_type": mime_type, "data": data_base64}
