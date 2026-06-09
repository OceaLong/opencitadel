#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视觉 grounding 工具：区域裁剪/缩放后再观察。"""
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


class VisionGroundingTool(BaseTool):
    name: str = "vision_grounding"

    def __init__(self, sandbox: Sandbox, llm: LLM) -> None:
        super().__init__()
        self.sandbox = sandbox
        self._llm = llm

    def _guess_mime(self, filepath: str) -> str:
        mime, _ = mimetypes.guess_type(filepath)
        return mime or "image/png"

    @tool(
        name="inspect_image_region",
        description=(
            "对沙箱图片的指定区域进行裁剪放大后再分析（视觉 grounding）。"
            "坐标为归一化值 0-1，原点在左上角。"
        ),
        parameters={
            "filepath": {"type": "string", "description": "沙箱内图片路径"},
            "x": {"type": "number", "description": "区域左上角 x (0-1)"},
            "y": {"type": "number", "description": "区域左上角 y (0-1)"},
            "width": {"type": "number", "description": "区域宽度 (0-1)"},
            "height": {"type": "number", "description": "区域高度 (0-1)"},
            "prompt": {"type": "string", "description": "对裁剪区域的具体问题"},
        },
        required=["filepath", "x", "y", "width", "height"],
    )
    async def inspect_image_region(
            self,
            filepath: str,
            x: float,
            y: float,
            width: float,
            height: float,
            prompt: Optional[str] = None,
    ) -> ToolResult:
        if not vision_service.vision_enabled(self._llm):
            return ToolResult(success=False, message="当前模型未开启多模态能力。")

        mime_type = self._guess_mime(filepath)
        if not is_image_mime(mime_type):
            return ToolResult(success=False, message=f"非图片文件: {filepath}")

        try:
            file_data = await self.sandbox.download_file(filepath)
            image_bytes = file_data.read()
        except Exception as exc:
            return ToolResult(success=False, message=f"读取图片失败: {exc}")

        capabilities = vision_service.resolve_capabilities(self._llm)
        cropped = await asyncio.to_thread(
            vision_service.crop_image_region,
            image_bytes,
            mime_type,
            x, y, width, height,
            max_bytes=capabilities.max_image_bytes,
        )
        user_prompt = (prompt or "请详细描述这个裁剪区域的内容，若有文字请 OCR。").strip()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                build_image_content_part(cropped, mime_type),
            ],
        }]
        try:
            response = await self._llm.invoke(messages)
        except Exception as exc:
            return ToolResult(success=False, message=f"区域分析失败: {exc}")

        content = response.get("content") or response.get("reasoning_content") or ""
        if not content:
            return ToolResult(success=False, message="模型未返回有效分析结果")

        data: Dict[str, Any] = {
            "filepath": filepath,
            "region": {"x": x, "y": y, "width": width, "height": height},
            "analysis": content,
        }
        return ToolResult(success=True, message="区域分析完成", data=data)
