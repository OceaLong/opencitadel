#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OCR fallback for scanned PDF pages using vision-capable LLM."""
import base64
import logging
from typing import List, Optional

from app.domain.external.llm import LLM
from app.domain.services.document_service import document_to_vision_attachments
from app.domain.services.knowledge_base.parsers import PageBlock
from app.domain.utils.vision import build_image_content_part

logger = logging.getLogger(__name__)

_OCR_PROMPT = "请提取图片中的全部可见文字，保持段落结构，只输出识别到的正文，不要解释。"


async def ocr_pdf_to_blocks(
        data: bytes,
        llm: Optional[LLM],
        *,
        max_pages: int,
) -> tuple[List[PageBlock], Optional[str]]:
    if not llm or max_pages <= 0:
        return [], "OCR 未执行：LLM 不可用或 max_pages 为 0"
    try:
        _text, page_images = await document_to_vision_attachments(data, "application/pdf")
    except Exception as exc:
        logger.warning("PDF 页渲染失败: %s", exc)
        return [], f"OCR 页渲染失败: {exc}"
    if not page_images:
        return [], "OCR 未执行：无法渲染 PDF 页面图像"

    blocks: list[PageBlock] = []
    warnings: list[str] = []
    for page in page_images[:max_pages]:
        page_no = int(page.get("page") or len(blocks) + 1)
        try:
            image_bytes = base64.b64decode(page["data_base64"])
            mime = page.get("mime_type") or "image/jpeg"
            response = await llm.invoke(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        build_image_content_part(image_bytes, mime),
                    ],
                }]
            )
            text = str(response.get("content") or response.get("reasoning_content") or "").strip()
            if text:
                blocks.append(PageBlock(page_no=page_no, heading_path=f"Page {page_no}", text=text))
        except Exception as exc:
            logger.warning("OCR 页面失败 page=%s: %s", page_no, exc)
            warnings.append(f"p{page_no}: {exc}")
    warning = "；".join(warnings) if warnings else None
    if not blocks and not warning:
        warning = "OCR 未识别到文本"
    return blocks, warning
