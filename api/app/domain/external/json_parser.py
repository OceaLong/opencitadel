from typing import Any, Protocol


class JSONParser(Protocol):
    """JSON解析器，用于解析json字符串并修复"""

    async def invoke(self, text: str, default_value: Any | None = None) -> dict | list | Any:
        """调用函数，用于将传递进来的文本进行解析并返回"""
        ...
