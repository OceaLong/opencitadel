from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.domain.models.inference import InferenceCapabilities


class LLM(Protocol):
    """用于Agent应用与LLM进行交互的接口协议"""

    async def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """传递消息列表、工具列表、响应格式、工具选择策略调用LLM接口"""
        ...

    async def stream_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式调用 LLM，yield delta 字典: content/reasoning_content/tool_calls/index."""
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
    def capabilities(self) -> "InferenceCapabilities":
        """只读属性，模型能力描述"""
        ...
