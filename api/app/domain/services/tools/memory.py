#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Awaitable, Callable, Optional

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool

SaveMemoryFn = Callable[[str, str, list, str], Awaitable[dict]]


class MemoryTool(BaseTool):
    """长期记忆保存工具"""

    name: str = "memory"

    def __init__(self, save_fn: SaveMemoryFn, session_id: str) -> None:
        super().__init__()
        self._save_fn = save_fn
        self._session_id = session_id

    @tool(
        name="memory_save",
        description="将重要信息保存到长期记忆中，供未来会话召回使用",
        parameters={
            "title": {"type": "string", "description": "记忆标题，简短概括"},
            "content": {"type": "string", "description": "记忆内容，具体事实或偏好"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表，便于分类检索",
            },
            "scope": {
                "type": "string",
                "enum": ["global", "session"],
                "description": "作用域：global跨会话，session仅当前会话",
            },
        },
        required=["title", "content"],
    )
    async def memory_save(
            self,
            title: str,
            content: str,
            tags: Optional[list] = None,
            scope: str = "global",
    ) -> ToolResult:
        result = await self._save_fn(title, content, tags or [], scope)
        return ToolResult(success=True, message=f"记忆已保存: {title}", data=result)
