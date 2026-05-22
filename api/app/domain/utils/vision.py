#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
from typing import Any, Dict, List, Optional

from app.domain.models.message import VisionAttachment


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


def build_multipart_user_content(
        text: str,
        vision_attachments: Optional[List[VisionAttachment]] = None,
) -> List[Dict[str, Any]]:
    """将文本与图片附件组合为 multipart user content。"""
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for attachment in vision_attachments or []:
        if not is_image_mime(attachment.mime_type):
            continue
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
        parts = build_multipart_user_content(text, vision_attachments)
        if len(parts) == 1 and parts[0].get("type") == "text":
            return {"role": "user", "content": text}
        return {"role": "user", "content": parts}
    return {"role": "user", "content": text}
