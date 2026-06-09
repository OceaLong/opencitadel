#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import asyncio
import logging
import mimetypes
from typing import Any, Dict, Optional

from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.services import vision_service
from app.domain.utils.vision import build_image_content_part, is_image_mime
from .base import BaseTool, tool

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "请详细描述这张图片的内容。若包含文字，请尽量完整转录(OCR)。"
    "用中文回答，结构清晰。"
)


class VisionTool(BaseTool):
    """视觉识别工具：对沙箱内图片进行 OCR/描述分析。"""
    name: str = "vision"

    def __init__(self, sandbox: Sandbox, llm: LLM) -> None:
        super().__init__()
        self.sandbox = sandbox
        self._llm = llm

    def _guess_mime(self, filepath: str) -> str:
        mime, _ = mimetypes.guess_type(filepath)
        return mime or "image/png"

    @tool(
        name="analyze_image",
        description=(
            "对沙箱中的图片文件进行视觉识别、OCR 或按自定义问题分析。"
            "适用于读取截图、图表、UI 界面或文档扫描件。"
        ),
        parameters={
            "filepath": {
                "type": "string",
                "description": "沙箱内图片文件的绝对路径",
            },
            "prompt": {
                "type": "string",
                "description": "可选，对图片提出的具体问题或分析指令",
            },
        },
        required=["filepath"],
    )
    async def analyze_image(
            self,
            filepath: str,
            prompt: Optional[str] = None,
    ) -> ToolResult:
        if not vision_service.vision_enabled(self._llm):
            return ToolResult(
                success=False,
                message="当前模型未开启多模态能力，请在设置中为会话选择支持视觉的模型。",
            )

        mime_type = self._guess_mime(filepath)
        if not is_image_mime(mime_type):
            return ToolResult(success=False, message=f"非图片文件，无法分析: {filepath}")

        try:
            file_data = await self.sandbox.download_file(filepath)
            image_bytes = file_data.read()
        except Exception as exc:
            logger.warning("读取沙箱图片失败 path=%s: %s", filepath, exc)
            return ToolResult(success=False, message=f"读取图片失败: {exc}")

        capabilities = vision_service.resolve_capabilities(self._llm)
        if len(image_bytes) > capabilities.max_image_bytes:
            image_bytes = await asyncio.to_thread(
                vision_service._compress_image_bytes,
                image_bytes,
                mime_type,
                capabilities.max_image_bytes,
            )

        user_prompt = (prompt or _DEFAULT_PROMPT).strip()
        image_part = build_image_content_part(image_bytes, mime_type)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                image_part,
            ],
        }]

        try:
            response = await self._llm.invoke(messages)
        except Exception as exc:
            logger.error("视觉分析 LLM 调用失败: %s", exc, exc_info=True)
            return ToolResult(success=False, message=f"视觉分析失败: {exc}")

        content = response.get("content") or ""
        if not content and response.get("reasoning_content"):
            content = response.get("reasoning_content") or ""

        if not content:
            return ToolResult(success=False, message="模型未返回有效的图片分析结果")

        data: Dict[str, Any] = {
            "filepath": filepath,
            "mime_type": mime_type,
            "analysis": content,
            "prompt": user_prompt,
        }
        return ToolResult(success=True, message="图片分析完成", data=data)
