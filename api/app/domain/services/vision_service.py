#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import io
import logging
import uuid
from typing import Any, Dict, List, Optional, Protocol, Union

from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.external.llm import LLM
from app.domain.models.file import File
from app.domain.models.llm_model import ModelCapabilities
from app.domain.models.message import MediaAttachment, VisionAttachment
from app.domain.models.multimodal import (
    CONTENT_TYPE_IMAGE_REF,
    CONTENT_TYPE_TEXT,
    build_audio_part,
    build_text_part,
)
from app.domain.utils.vision import (
    build_image_content_part,
    build_image_content_part_from_base64,
    filter_valid_vision_attachments,
    is_audio_mime,
    is_image_mime,
    vision_attachment_byte_size,
)

logger = logging.getLogger(__name__)

_IMAGE_REF_TYPE = CONTENT_TYPE_IMAGE_REF
_FALLBACK_IMAGE_NOTE = "原始消息包含图片附件，因模型服务连接异常已省略图片内容。"


class _SupportsCapabilities(Protocol):
    @property
    def capabilities(self) -> ModelCapabilities: ...

    @property
    def supports_multimodal(self) -> bool: ...


def resolve_capabilities(llm: LLM) -> ModelCapabilities:
    caps = getattr(llm, "capabilities", None)
    if isinstance(caps, ModelCapabilities):
        return caps
    if getattr(llm, "supports_multimodal", False):
        return ModelCapabilities(vision=True)
    return ModelCapabilities()


def vision_enabled(llm: LLM) -> bool:
    return resolve_capabilities(llm).vision


def build_image_ref_part(ref_url: str, mime_type: str = "image/png") -> Dict[str, Any]:
    return {"type": _IMAGE_REF_TYPE, "ref": ref_url, "mime_type": mime_type}


def _compress_image_bytes(image_bytes: bytes, mime_type: str, max_bytes: int) -> bytes:
    if len(image_bytes) <= max_bytes:
        return image_bytes
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，跳过图片压缩")
        return image_bytes

    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    quality = 85
    scale = 1.0
    output = image_bytes
    while len(output) > max_bytes and (quality > 35 or scale > 0.2):
        if scale > 0.2:
            scale *= 0.8
            resized = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        else:
            resized = image
        buffer = io.BytesIO()
        save_format = "PNG" if mime_type.endswith("png") else "JPEG"
        save_kwargs = {"optimize": True}
        if save_format == "JPEG":
            save_kwargs["quality"] = quality
            quality -= 10
        resized.save(buffer, format=save_format, **save_kwargs)
        output = buffer.getvalue()
    return output


def crop_image_region(
        image_bytes: bytes,
        mime_type: str,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        max_bytes: int,
) -> bytes:
    """按归一化坐标 (0-1) 裁剪图片区域，用于视觉 grounding。"""
    try:
        from PIL import Image
    except ImportError:
        return image_bytes

    image = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = image.size
    left = max(0, int(x * img_w))
    top = max(0, int(y * img_h))
    right = min(img_w, int((x + width) * img_w))
    bottom = min(img_h, int((y + height) * img_h))
    if right <= left or bottom <= top:
        return image_bytes
    cropped = image.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    fmt = "PNG" if mime_type.endswith("png") else "JPEG"
    cropped.save(buffer, format=fmt, optimize=True)
    return _compress_image_bytes(buffer.getvalue(), mime_type, max_bytes)


def build_file_public_url(file: File) -> str:
    from core.config import get_settings

    settings = get_settings()
    return f"https://{settings.cos_bucket}.cos.{settings.cos_region}.myqcloud.com/{file.key}"


async def upload_image_bytes_to_storage(
        file_storage: FileStorage,
        image_bytes: bytes,
        mime_type: str = "image/png",
        filename: Optional[str] = None,
) -> str:
    """上传图片到 COS，返回公开 URL。"""
    name = filename or f"{uuid.uuid4()}.png"
    stream = io.BytesIO(image_bytes)
    upload = FileUploadPayload(
        file=stream,
        filename=name,
        size=len(image_bytes),
        content_type=mime_type,
    )
    stored = await file_storage.upload_file(upload)
    return build_file_public_url(stored)


def _attachment_to_memory_part(
        attachment: Union[VisionAttachment, MediaAttachment],
        capabilities: ModelCapabilities,
) -> Optional[Dict[str, Any]]:
    media_type = getattr(attachment, "media_type", "image")

    if media_type == "audio" and attachment.data_base64:
        return build_audio_part(attachment.data_base64, attachment.mime_type or "audio/wav")

    if media_type == "video_frame" and attachment.ref_url:
        return build_image_ref_part(attachment.ref_url, attachment.mime_type or "image/jpeg")

    # 优先 image_ref（跨 step 复用 URL，避免重复内联）
    if attachment.ref_url:
        return build_image_ref_part(attachment.ref_url, attachment.mime_type)

    if attachment.ref_url and capabilities.image_encoding == "url":
        return build_image_ref_part(attachment.ref_url, attachment.mime_type)

    if not attachment.data_base64:
        return None
    try:
        image_bytes = base64.b64decode(attachment.data_base64, validate=False)
    except Exception:
        return None
    image_bytes = _compress_image_bytes(image_bytes, attachment.mime_type, capabilities.max_image_bytes)
    return build_image_content_part(image_bytes, attachment.mime_type)


def build_user_message(
        text: str,
        vision_attachments: Optional[List[Union[VisionAttachment, MediaAttachment]]] = None,
        llm: Optional[LLM] = None,
) -> Dict[str, Any]:
    """构建 user 消息；多模态时 content 为 parts 数组（优先 image_ref）。"""
    capabilities = resolve_capabilities(llm) if llm else ModelCapabilities()
    if not vision_attachments:
        return {"role": "user", "content": text}

    has_audio = any(getattr(a, "media_type", "image") == "audio" for a in vision_attachments)
    if not capabilities.vision and not (capabilities.audio and has_audio):
        return {"role": "user", "content": text}

    valid_attachments = filter_valid_vision_attachments(
        vision_attachments,
        max_bytes=capabilities.max_image_bytes,
    )[: capabilities.max_images_per_request]
    audio_attachments = [
        a for a in (vision_attachments or [])
        if getattr(a, "media_type", "image") == "audio" and a.data_base64
    ]
    if not valid_attachments and not audio_attachments:
        return {"role": "user", "content": text}

    parts: List[Dict[str, Any]] = []
    if text:
        parts.append(build_text_part(text))
    seen_refs: set[str] = set()
    for attachment in valid_attachments:
        if attachment.ref_url and attachment.ref_url in seen_refs:
            continue
        part = _attachment_to_memory_part(attachment, capabilities)
        if part:
            if attachment.ref_url:
                seen_refs.add(attachment.ref_url)
            parts.append(part)

    if capabilities.audio:
        for attachment in audio_attachments:
            part = _attachment_to_memory_part(attachment, capabilities)
            if part:
                parts.append(part)

    if len(parts) == 1 and parts[0].get("type") == CONTENT_TYPE_TEXT:
        return {"role": "user", "content": text}
    logger.info(
        "构建多模态 user 消息: text_len=%s image_count=%s encoding=%s",
        len(text or ""),
        len(valid_attachments),
        capabilities.image_encoding,
    )
    return {"role": "user", "content": parts}


def memory_contains_image_refs(messages: List[Dict[str, Any]], refs: List[str]) -> bool:
    """检查 memory 中是否已包含相同 image_ref。"""
    if not refs:
        return False
    ref_set = set(refs)
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == _IMAGE_REF_TYPE:
                if part.get("ref") in ref_set:
                    return True
    return False


def inflate_messages_for_llm(
        messages: List[Dict[str, Any]],
        llm: Optional[LLM] = None,
) -> List[Dict[str, Any]]:
    """发送 LLM 前将 image_ref 还原为 image_url。"""
    capabilities = resolve_capabilities(llm) if llm else ModelCapabilities()
    inflated: List[Dict[str, Any]] = []
    for message in messages:
        cleaned = {k: v for k, v in message.items() if not k.startswith("_")}
        content = cleaned.get("content")
        if not isinstance(content, list):
            inflated.append(cleaned)
            continue
        new_parts: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == _IMAGE_REF_TYPE:
                ref_url = part.get("ref")
                mime_type = part.get("mime_type") or "image/png"
                if ref_url:
                    if capabilities.image_encoding == "url" or str(ref_url).startswith("http"):
                        new_parts.append({"type": "image_url", "image_url": {"url": ref_url}})
                    else:
                        new_parts.append(build_image_content_part_from_base64(ref_url, mime_type))
                continue
            new_parts.append(part)
        cleaned["content"] = new_parts if new_parts else content
        inflated.append(cleaned)
    return inflated


def strip_images_for_tool_call(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """vision_with_tools=False 时，带 tools 的请求临时剥离图片。"""
    stripped: List[Dict[str, Any]] = []
    for message in messages:
        cleaned = dict(message)
        content = cleaned.get("content")
        if not isinstance(content, list):
            stripped.append(cleaned)
            continue
        text_parts = [
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        had_image = any(
            isinstance(p, dict) and p.get("type") in {_IMAGE_REF_TYPE, "image_url"}
            for p in content
        )
        if had_image and text_parts:
            cleaned["content"] = "\n".join(text_parts) + "\n[图片已在先前轮次中分析]"
        elif had_image:
            cleaned["content"] = "[图片已在先前轮次中分析]"
        stripped.append(cleaned)
    return stripped


def compress_messages_for_retry(
        messages: List[Dict[str, Any]],
        max_bytes: int,
) -> List[Dict[str, Any]]:
    """413 等 payload 过大时压缩 messages 中的图片。"""
    compressed: List[Dict[str, Any]] = []
    for message in messages:
        cleaned = dict(message)
        content = cleaned.get("content")
        if not isinstance(content, list):
            compressed.append(cleaned)
            continue
        new_parts: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:") and ";base64," in url:
                    header, encoded = url.split(";base64,", 1)
                    mime_type = header.replace("data:", "")
                    image_bytes = _compress_image_bytes(
                        base64.b64decode(encoded),
                        mime_type,
                        max_bytes,
                    )
                    new_parts.append(build_image_content_part(image_bytes, mime_type))
                    continue
            new_parts.append(part)
        cleaned["content"] = new_parts
        compressed.append(cleaned)
    return compressed


async def build_screenshot_messages(
        function_name: str,
        result_data: Dict[str, Any],
        llm: LLM,
        file_storage: Optional[FileStorage] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """构建浏览器截图工具返回给 LLM 的文本与可选截图 user 消息。"""
    import json

    interactive_elements = result_data.get("interactive_elements") or []
    elements_text = "\n".join(interactive_elements[:100])
    text_sections: List[str] = []
    if elements_text:
        text_sections.append(f"Interactive elements:\n{elements_text}")
    if not text_sections:
        text_sections.append("Browser screenshot captured.")

    summary = {
        "success": True,
        "message": "",
        "interactive_elements": interactive_elements[:100],
        "note": "Page screenshot attached in the following user message.",
    }
    extra_messages: List[Dict[str, Any]] = []
    screenshot_base64 = result_data.get("screenshot_base64")
    screenshot_ref = result_data.get("screenshot_ref")
    capabilities = resolve_capabilities(llm)

    # 优先上传 COS 获得 screenshot_ref
    if not screenshot_ref and screenshot_base64 and file_storage:
        try:
            screenshot_bytes = base64.b64decode(screenshot_base64, validate=False)
            if len(screenshot_bytes) <= capabilities.max_image_bytes:
                screenshot_ref = await upload_image_bytes_to_storage(
                    file_storage,
                    screenshot_bytes,
                    "image/png",
                )
        except Exception as exc:
            logger.warning("截图上传 COS 失败，回退 base64: %s", exc)

    if screenshot_ref and capabilities.vision:
        extra_messages.append({
            "role": "user",
            "content": [
                build_text_part(
                    f"Screenshot from `{function_name}` "
                    f"(see interactive element indices in tool result above):"
                ),
                build_image_ref_part(screenshot_ref, "image/png"),
            ],
        })
    elif screenshot_base64 and capabilities.vision:
        try:
            screenshot_bytes = base64.b64decode(screenshot_base64, validate=False)
        except Exception:
            screenshot_bytes = b""
        if len(screenshot_bytes) > capabilities.max_image_bytes:
            summary["note"] = "Page screenshot omitted due to size limit."
        else:
            extra_messages.append({
                "role": "user",
                "content": [
                    build_text_part(
                        f"Screenshot from `{function_name}` "
                        f"(see interactive element indices in tool result above):"
                    ),
                    build_image_content_part(screenshot_bytes, "image/png"),
                ],
            })
    return json.dumps(summary, ensure_ascii=False), extra_messages


async def prepare_vision_attachments_from_files(
        files: List[File],
        llm: LLM,
        file_storage: FileStorage,
) -> List[VisionAttachment]:
    """为多模态模型构建用户图片附件，优先使用 URL 引用。"""
    if not vision_enabled(llm):
        return []

    capabilities = resolve_capabilities(llm)
    attachments: List[VisionAttachment] = []
    for file in files:
        if not is_image_mime(file.mime_type):
            continue
        try:
            file_data, stored_file = await file_storage.download_file(file.id)
            image_bytes = file_data.read()
            if len(image_bytes) > capabilities.max_image_bytes:
                image_bytes = _compress_image_bytes(
                    image_bytes,
                    file.mime_type,
                    capabilities.max_image_bytes,
                )
            ref_url = build_file_public_url(stored_file) if stored_file.key else ""
            if ref_url:
                attachments.append(VisionAttachment(
                    mime_type=file.mime_type,
                    ref_url=ref_url,
                ))
            else:
                attachments.append(VisionAttachment(
                    mime_type=file.mime_type,
                    data_base64=base64.b64encode(image_bytes).decode("ascii"),
                    ref_url=ref_url,
                ))
        except Exception as exc:
            logger.warning("构建 vision 附件失败 file_id=%s: %s", file.id, exc)
    return attachments


async def prepare_media_attachments_from_files(
        files: List[File],
        llm: LLM,
        file_storage: FileStorage,
) -> List[MediaAttachment]:
    """构建通用多模态附件（图片/音频/视频帧）。"""
    attachments: List[MediaAttachment] = []
    capabilities = resolve_capabilities(llm)

    for file in files:
        if is_image_mime(file.mime_type):
            attachments.extend(await prepare_vision_attachments_from_files([file], llm, file_storage))
        elif is_audio_mime(file.mime_type) and capabilities.audio:
            try:
                file_data, _ = await file_storage.download_file(file.id)
                audio_bytes = file_data.read()
                attachments.append(MediaAttachment(
                    mime_type=file.mime_type,
                    data_base64=base64.b64encode(audio_bytes).decode("ascii"),
                    media_type="audio",
                ))
            except Exception as exc:
                logger.warning("构建 audio 附件失败 file_id=%s: %s", file.id, exc)
    return attachments
