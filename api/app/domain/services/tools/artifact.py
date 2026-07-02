#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Awaitable, Callable, List, Optional

from app.domain.models.event import BaseEvent
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool

WriteArtifactFn = Callable[..., Awaitable[tuple[dict, BaseEvent]]]
FinalizeArtifactFn = Callable[[str], Awaitable[tuple[dict, BaseEvent]]]


class ArtifactTool(BaseTool):
    name: str = "artifact"

    def __init__(
            self,
            write_fn: WriteArtifactFn,
            finalize_fn: FinalizeArtifactFn,
    ) -> None:
        super().__init__()
        self._write_fn = write_fn
        self._finalize_fn = finalize_fn
        self._pending_events: List[BaseEvent] = []

    def drain_events(self) -> List[BaseEvent]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    @tool(
        name="artifact_write",
        description="创建或更新会话交付物（文档 Markdown 或网页 HTML）。产出最终结果时必须使用此工具，而非 write_file。",
        parameters={
            "artifact_id": {
                "type": "string",
                "description": "已有交付物 ID；留空则创建新交付物",
            },
            "kind": {
                "type": "string",
                "enum": ["doc", "web"],
                "description": "交付物类型：doc=Markdown 文档，web=HTML 网页",
            },
            "title": {"type": "string", "description": "交付物标题"},
            "content": {"type": "string", "description": "完整内容（Markdown 或 HTML）"},
        },
        required=["kind", "title", "content"],
    )
    async def artifact_write(
            self,
            kind: str,
            title: str,
            content: str,
            artifact_id: Optional[str] = None,
    ) -> ToolResult:
        data, event = await self._write_fn(
            artifact_id=artifact_id,
            kind=kind,
            title=title,
            content=content,
        )
        self._pending_events.append(event)
        return ToolResult(
            success=True,
            message=f"交付物已保存: {data.get('title', title)}",
            data=data,
        )

    @tool(
        name="artifact_finalize",
        description="将交付物标记为定稿，不再自动更新",
        parameters={
            "artifact_id": {"type": "string", "description": "交付物 ID"},
        },
        required=["artifact_id"],
    )
    async def artifact_finalize(self, artifact_id: str) -> ToolResult:
        data, event = await self._finalize_fn(artifact_id)
        self._pending_events.append(event)
        return ToolResult(success=True, message="交付物已定稿", data=data)
