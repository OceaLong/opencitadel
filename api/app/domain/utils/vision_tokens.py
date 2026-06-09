#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vision token 估算与消息体积统计。"""
import base64
import io
import re
from typing import Any, Dict, List, Tuple

from app.domain.models.multimodal import IMAGE_PART_TYPES, is_image_part

_DATA_URL_PATTERN = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

# OpenAI gpt-4o 风格：低分辨率固定 85 tokens；高分辨率按 512px tile 计
_LOW_DETAIL_IMAGE_TOKENS = 85
_TILE_SIZE = 512
_TOKENS_PER_TILE = 170


def _decode_image_dimensions(image_bytes: bytes) -> Tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.width, img.height
    except Exception:
        # 无法解析时按 1024x1024 估算
        return 1024, 1024


def estimate_image_tokens(width: int, height: int, *, detail: str = "high") -> int:
    if detail == "low":
        return _LOW_DETAIL_IMAGE_TOKENS
    # high detail: scale to fit 2048x2048, then tile 512x512
    max_side = max(width, height)
    if max_side > 2048:
        scale = 2048 / max_side
        width = int(width * scale)
        height = int(height * scale)
    tiles_w = (width + _TILE_SIZE - 1) // _TILE_SIZE
    tiles_h = (height + _TILE_SIZE - 1) // _TILE_SIZE
    return tiles_w * tiles_h * _TOKENS_PER_TILE + _LOW_DETAIL_IMAGE_TOKENS


def estimate_image_bytes_tokens(image_bytes: bytes, *, detail: str = "high") -> int:
    width, height = _decode_image_dimensions(image_bytes)
    return estimate_image_tokens(width, height, detail=detail)


def _image_bytes_from_part(part: Dict[str, Any]) -> bytes:
    part_type = part.get("type")
    if part_type == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        if url.startswith("data:") and ";base64," in url:
            encoded = url.split(";base64,", 1)[1]
            try:
                return base64.b64decode(encoded, validate=False)
            except Exception:
                return b""
        # 远程 URL 无法获知体积，按 200KB 估算
        return b"x" * 200_000
    if part_type == "image_ref":
        ref = part.get("ref", "")
        if ref and not str(ref).startswith("http"):
            try:
                return base64.b64decode(ref, validate=False)
            except Exception:
                return b""
        return b"x" * 200_000
    return b""


def count_message_image_stats(messages: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """返回 (image_count, total_bytes, estimated_vision_tokens)。"""
    image_count = 0
    total_bytes = 0
    vision_tokens = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not is_image_part(part):
                continue
            image_count += 1
            image_bytes = _image_bytes_from_part(part)
            total_bytes += len(image_bytes)
            if image_bytes:
                vision_tokens += estimate_image_bytes_tokens(image_bytes)
            else:
                vision_tokens += _LOW_DETAIL_IMAGE_TOKENS
    return image_count, total_bytes, vision_tokens


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算消息列表总 token（文本 + vision）。"""
    text_chars = 0
    _, _, vision_tokens = count_message_image_stats(messages)
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_chars += len(str(part.get("text", "")))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            text_chars += len(str(tool_calls))
    return (text_chars // 4) + vision_tokens
