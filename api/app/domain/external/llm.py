#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.models.llm_model import ModelCapabilities


class LLM(Protocol):
    """用于Agent应用与LLM进行交互的接口协议"""

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        """传递消息列表、工具列表、响应格式、工具选择策略调用LLM接口"""
        ...

    @property
    def model_name(self) -> str:
        """只读属性，返回LLM的名字"""
        ...

    @property
    def temperature(self) -> float:
        """只读属性，返回LLM的温度"""
        ...

    @property
    def max_tokens(self) -> int:
        """只读属性，返回LLM的最大生成token数"""
        ...

    @property
    def supports_multimodal(self) -> bool:
        """只读属性，模型是否支持多模态（图片）理解"""
        ...

    @property
    def capabilities(self) -> "ModelCapabilities":
        """只读属性，模型能力描述"""
        ...
