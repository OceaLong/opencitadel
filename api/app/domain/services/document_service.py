#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档版面解析：PDF/扫描件分页渲染 + 文本抽取。"""
import asyncio
import base64
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_PAGES = 20
_MAX_TEXT_CHARS = 12000


async def parse_document_bytes(
        file_bytes: bytes,
        mime_type: str,
        filename: str = "",
        *,
        max_pages: int = _MAX_PAGES,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    解析文档，返回 (合并文本, 页面列表)。
    页面列表每项: {page, text, image_base64?, mime_type?}
    """
    lower_name = (filename or "").lower()
    if mime_type == "application/pdf" or lower_name.endswith(".pdf"):
        return await asyncio.to_thread(_parse_pdf, file_bytes, max_pages)
    if mime_type.startswith("text/") or lower_name.endswith((".txt", ".md", ".csv", ".json")):
        text = _decode_text(file_bytes)
        return text[:_MAX_TEXT_CHARS], [{"page": 1, "text": text[:_MAX_TEXT_CHARS]}]
    return "", []


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes, max_pages: int) -> Tuple[str, List[Dict[str, Any]]]:
    pages: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        for idx, page in enumerate(reader.pages[:max_pages]):
            page_text = (page.extract_text() or "").strip()
            entry: Dict[str, Any] = {"page": idx + 1, "text": page_text}
            pages.append(entry)
            if page_text:
                text_parts.append(f"--- Page {idx + 1} ---\n{page_text}")
    except ImportError:
        logger.warning("pypdf 未安装，PDF 文本抽取不可用")
    except Exception as exc:
        logger.warning("PDF 解析失败: %s", exc)

    # 尝试将 PDF 页渲染为图像（需 pdf2image + poppler，可选）
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(data, first_page=1, last_page=min(max_pages, 5), dpi=150)
        for idx, image in enumerate(images):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            if idx < len(pages):
                pages[idx]["image_base64"] = img_b64
                pages[idx]["mime_type"] = "image/jpeg"
            else:
                pages.append({"page": idx + 1, "text": "", "image_base64": img_b64, "mime_type": "image/jpeg"})
    except ImportError:
        # pdf2image 为可选依赖；未安装时仅返回文本抽取结果
        logger.debug("pdf2image 未安装，跳过 PDF 页渲染")
    except Exception as exc:
        logger.debug("PDF 页渲染跳过: %s", exc)

    combined = "\n\n".join(text_parts)[:_MAX_TEXT_CHARS]
    return combined, pages


async def document_to_vision_attachments(
        file_bytes: bytes,
        mime_type: str,
        filename: str = "",
) -> Tuple[str, List[Dict[str, Any]]]:
    """返回文档文本 + 可用于 vision 的页面图像 parts 元数据。"""
    text, pages = await parse_document_bytes(file_bytes, mime_type, filename)
    image_parts = []
    for page in pages:
        if page.get("image_base64"):
            image_parts.append({
                "page": page["page"],
                "mime_type": page.get("mime_type", "image/jpeg"),
                "data_base64": page["image_base64"],
            })
    return text, image_parts
