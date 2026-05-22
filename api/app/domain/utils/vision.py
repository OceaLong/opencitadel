#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import logging
from typing import Any, Dict, List, Optional

from app.domain.models.message import VisionAttachment

logger = logging.getLogger(__name__)

# 单张图片原始字节上限（约 20MB），超过则跳过以避免 provider 长时间无响应
MAX_VISION_IMAGE_BYTES = 20 * 1024 * 1024


def is_image_mime(mime_type: str) -> bool:
    """判断 mime 是否为图片类型。"""
    return bool(mime_type) and mime_type.lower().startswith("image/")


def build_image_content_part(image_bytes: bytes, mime_type: str) -> Dict[str, Any]:
    """将图片字节转为 OpenAI-compatible image_url content part。"""
    if not is_image_mime(mime_type):
        raise ValueError(f"不支持的图片 mime 类型: {mime_type}")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def build_image_content_part_from_base64(data_base64: str, mime_type: str) -> Dict[str, Any]:
    """从 base64 字符串构建 image_url content part。"""
    return build_image_content_part(base64.b64decode(data_base64), mime_type)


def vision_attachment_byte_size(attachment: VisionAttachment) -> int:
    """估算 vision 附件解码后的原始字节大小。"""
    if not attachment.data_base64:
        return 0
    try:
        return len(base64.b64decode(attachment.data_base64, validate=False))
    except Exception:
        return len(attachment.data_base64)


def filter_valid_vision_attachments(
        vision_attachments: Optional[List[VisionAttachment]] = None,
        *,
        max_bytes: int = MAX_VISION_IMAGE_BYTES,
) -> List[VisionAttachment]:
    """过滤无效或过大的图片附件，避免多模态请求长时间阻塞。"""
    valid: List[VisionAttachment] = []
    for attachment in vision_attachments or []:
        if not is_image_mime(attachment.mime_type):
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
        vision_attachments: Optional[List[VisionAttachment]] = None,
) -> List[Dict[str, Any]]:
    """将文本与图片附件组合为 multipart user content。"""
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for attachment in filter_valid_vision_attachments(vision_attachments):
        parts.append(
            build_image_content_part_from_base64(
                attachment.data_base64,
                attachment.mime_type,
            )
        )
    return parts


def build_user_message(
        text: str,
        vision_attachments: Optional[List[VisionAttachment]] = None,
        *,
        supports_multimodal: bool = False,
) -> Dict[str, Any]:
    """构建 user 消息；多模态时 content 为 parts 数组，否则为纯文本。"""
    if supports_multimodal and vision_attachments:
        valid_attachments = filter_valid_vision_attachments(vision_attachments)
        if valid_attachments:
            logger.info(
                "构建多模态 user 消息: text_len=%s image_count=%s",
                len(text or ""),
                len(valid_attachments),
            )
        parts = build_multipart_user_content(text, valid_attachments)
        if len(parts) == 1 and parts[0].get("type") == "text":
            return {"role": "user", "content": text}
        return {"role": "user", "content": parts}
    return {"role": "user", "content": text}
