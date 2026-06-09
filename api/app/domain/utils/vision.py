#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""低层 vision 工具函数（content part 构建、MIME 判断、附件过滤）。"""
import base64
import logging
from typing import Any, Dict, List, Optional, Union

from app.domain.models.message import MediaAttachment, VisionAttachment
from app.domain.models.multimodal import CONTENT_TYPE_IMAGE_URL

logger = logging.getLogger(__name__)

# 单张图片原始字节上限（约 20MB），超过则跳过以避免 provider 长时间无响应
MAX_VISION_IMAGE_BYTES = 20 * 1024 * 1024


def is_image_mime(mime_type: str) -> bool:
    """判断 mime 是否为图片类型。"""
    return bool(mime_type) and mime_type.lower().startswith("image/")


def is_audio_mime(mime_type: str) -> bool:
    return bool(mime_type) and mime_type.lower().startswith("audio/")


def is_video_mime(mime_type: str) -> bool:
    return bool(mime_type) and mime_type.lower().startswith("video/")


def build_image_content_part(image_bytes: bytes, mime_type: str) -> Dict[str, Any]:
    """将图片字节转为 OpenAI-compatible image_url content part。"""
    if not is_image_mime(mime_type):
        raise ValueError(f"不支持的图片 mime 类型: {mime_type}")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": CONTENT_TYPE_IMAGE_URL,
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def build_image_content_part_from_base64(data_base64: str, mime_type: str) -> Dict[str, Any]:
    """从 base64 字符串构建 image_url content part。"""
    return build_image_content_part(base64.b64decode(data_base64), mime_type)


def vision_attachment_byte_size(attachment: Union[VisionAttachment, MediaAttachment]) -> int:
    """估算 vision 附件解码后的原始字节大小。"""
    if not attachment.data_base64:
        return 0
    try:
        return len(base64.b64decode(attachment.data_base64, validate=False))
    except Exception:
        return len(attachment.data_base64)


def filter_valid_vision_attachments(
        vision_attachments: Optional[List[Union[VisionAttachment, MediaAttachment]]] = None,
        *,
        max_bytes: int = MAX_VISION_IMAGE_BYTES,
) -> List[Union[VisionAttachment, MediaAttachment]]:
    """过滤无效或过大的图片附件，避免多模态请求长时间阻塞。"""
    valid: List[Union[VisionAttachment, MediaAttachment]] = []
    for attachment in vision_attachments or []:
        media_type = getattr(attachment, "media_type", "image")
        if media_type != "image" and not is_image_mime(attachment.mime_type):
            logger.debug("跳过非图片 vision 附件: mime=%s media_type=%s", attachment.mime_type, media_type)
            continue
        if not is_image_mime(attachment.mime_type) and media_type == "image":
            logger.debug("跳过非图片 vision 附件: mime=%s", attachment.mime_type)
            continue
        size_bytes = vision_attachment_byte_size(attachment)
        if size_bytes > max_bytes:
            logger.warning(
                "跳过过大的 vision 附件: mime=%s size_bytes=%s max_bytes=%s",
                attachment.mime_type,
                size_bytes,
                max_bytes,
            )
            continue
        valid.append(attachment)
    if vision_attachments:
        logger.info(
            "vision 附件过滤结果: total=%s valid=%s",
            len(vision_attachments),
            len(valid),
        )
    return valid


def build_multipart_user_content(
        text: str,
        vision_attachments: Optional[List[Union[VisionAttachment, MediaAttachment]]] = None,
        *,
        max_bytes: int = MAX_VISION_IMAGE_BYTES,
) -> List[Dict[str, Any]]:
    """将文本与图片附件组合为 multipart user content（内联 data_url）。"""
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for attachment in filter_valid_vision_attachments(vision_attachments, max_bytes=max_bytes):
        if attachment.data_base64:
            parts.append(
                build_image_content_part_from_base64(
                    attachment.data_base64,
                    attachment.mime_type,
                )
            )
    return parts


def build_user_message(
        text: str,
        vision_attachments: Optional[List[Union[VisionAttachment, MediaAttachment]]] = None,
        *,
        supports_multimodal: bool = False,
        llm=None,
) -> Dict[str, Any]:
    """构建 user 消息（兼容旧 supports_multimodal 签名，委托 vision_service）。"""
    from app.domain.services import vision_service

    if llm is not None:
        return vision_service.build_user_message(text, vision_attachments, llm=llm)
    if supports_multimodal and vision_attachments:
        from app.domain.models.llm_model import ModelCapabilities
        from types import SimpleNamespace

        fake_llm = SimpleNamespace(
            capabilities=ModelCapabilities(vision=True),
            supports_multimodal=True,
        )
        return vision_service.build_user_message(text, vision_attachments, llm=fake_llm)
    return {"role": "user", "content": text}
