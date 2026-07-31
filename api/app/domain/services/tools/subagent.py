#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sub-agent delegation tool for isolated parallel subtasks."""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

from app.domain.models.event import BaseEvent, SubAgentEvent, SubAgentEventStatus
from app.domain.models.knowledge_citation import (
    KnowledgeCitation,
    deduplicate_citations,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import SHELL_INTERACTIVE

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SubAgentOutcome:
    """Trusted structured result crossing one sub-agent boundary."""

    summary: str
    citations: tuple[KnowledgeCitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", str(self.summary or ""))
        object.__setattr__(
            self,
            "citations",
            tuple(deduplicate_citations(list(self.citations))),
        )


SubAgentRunner = Callable[..., Awaitable[str | SubAgentOutcome]]


class SubAgentTool(BaseTool):
    name: str = "subagent"

    def __init__(
            self,
            run_subagent: SubAgentRunner,
            *,
            max_concurrency: int = 3,
    ) -> None:
        super().__init__()
        self._run_subagent = run_subagent
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._pending_events: Dict[str, List[BaseEvent]] = {}

    def drain_events(self, subagent_id: Optional[str] = None) -> List[BaseEvent]:
        if subagent_id is not None:
            return self._pending_events.pop(subagent_id, [])
        events: List[BaseEvent] = []
        for queued in self._pending_events.values():
            events.extend(queued)
        self._pending_events = {}
        return events

    def _queue_event(self, event: BaseEvent) -> None:
        key = getattr(event, "subagent_id", "") or ""
        self._pending_events.setdefault(key, []).append(event)

    @tool(
        name="delegate_subtask",
        description=(
            "将可独立完成、无需依赖后续步骤上下文的子目标委派给隔离的子 Agent 执行。"
            "适合并行调研、独立文件处理等场景。返回子 Agent 的结果摘要。"
        ),
        parameters={
            "goal": {"type": "string", "description": "子目标的完整、自包含描述"},
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "(可选)限制子 Agent 可用工具名称列表",
            },
        },
        required=["goal"],
        policy=SHELL_INTERACTIVE,
    )
    async def delegate_subtask(
            self,
            goal: str,
            allowed_tools: Optional[List[str]] = None,
    ) -> ToolResult:
        subagent_id = f"subagent-{uuid.uuid4().hex[:8]}"
        self._queue_event(SubAgentEvent(
            subagent_id=subagent_id,
            goal=goal,
            status=SubAgentEventStatus.STARTED,
        ))
        async with self._semaphore:
            try:
                raw_outcome = await self._run_subagent(
                    goal=goal,
                    agent_name=subagent_id,
                    allowed_tools=allowed_tools,
                )
                if isinstance(raw_outcome, SubAgentOutcome):
                    summary = raw_outcome.summary
                    citations = list(raw_outcome.citations)
                else:
                    summary = str(raw_outcome or "")
                    citations = []
                self._queue_event(SubAgentEvent(
                    subagent_id=subagent_id,
                    goal=goal,
                    status=SubAgentEventStatus.COMPLETED,
                    result_preview=(summary or "")[:500],
                ))
                return ToolResult(
                    success=True,
                    message="子任务完成",
                    data={"summary": summary, "subagent_id": subagent_id},
                    citations=citations,
                )
            except Exception as exc:
                logger.exception("子 Agent 执行失败: %s", exc)
                self._queue_event(SubAgentEvent(
                    subagent_id=subagent_id,
                    goal=goal,
                    status=SubAgentEventStatus.FAILED,
                    error=str(exc),
                ))
                return ToolResult(
                    success=False,
                    message=f"子 Agent 执行失败: {exc}",
                    data={"subagent_id": subagent_id},
                )

    async def run_step(
            self,
            step_description: str,
            *,
            allowed_tools: Optional[List[str]] = None,
    ) -> ToolResult:
        """Flow-level parallel step execution entry (same isolation as delegate_subtask)."""
        return await self.delegate_subtask(step_description, allowed_tools=allowed_tools)
