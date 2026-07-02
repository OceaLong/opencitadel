#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图像生成工具：调用 provider 图像生成 API。"""
import logging
from typing import Optional

from app.domain.external.file_storage import FileStorage
from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel
from app.domain.models.tool_result import ToolResult
from app.domain.services import image_generation_service, vision_service
from .base import BaseTool, tool

logger = logging.getLogger(__name__)


class ImageGenerationTool(BaseTool):
    name: str = "image_generation"

    def __init__(
            self,
            llm: LLM,
            llm_model: LLMModel,
            file_storage: FileStorage,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._llm_model = llm_model
        self._file_storage = file_storage
        self._owner_user_id = owner_user_id
        self._team_id = team_id

    @tool(
        name="generate_image",
        description="根据文字描述生成图片，适用于插图、概念图、UI  mockup 等场景。",
        parameters={
            "prompt": {
                "type": "string",
                "description": "图像描述 prompt，尽量具体（主体、风格、构图）",
            },
            "size": {
                "type": "string",
                "description": "可选，尺寸如 1024x1024、1792x1024",
            },
        },
        required=["prompt"],
    )
    async def generate_image(
            self,
            prompt: str,
            size: Optional[str] = None,
    ) -> ToolResult:
        caps = vision_service.resolve_capabilities(self._llm)
        if not caps.image_generation:
            return ToolResult(
                success=False,
                message="当前模型未开启图像生成能力，请在模型设置中启用。",
            )
        url = await image_generation_service.generate_image(
            prompt,
            self._llm_model,
            self._file_storage,
            size=size or "1024x1024",
            owner_user_id=self._owner_user_id,
            team_id=self._team_id,
        )
        if not url:
            return ToolResult(success=False, message="图像生成失败，请检查 provider 配置。")
        return ToolResult(
            success=True,
            message="图像生成成功",
            data={"image_url": url, "prompt": prompt},
        )
