#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight RAG flow for codebase Ask mode."""
import logging
from typing import AsyncGenerator, Callable, List, Optional

from app.domain.external.browser import Browser
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.models.skill import Skill
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.tool_registry import ToolRegistry
from .base import BaseFlow, FlowStatus
from ...repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

CODE_ASK_PROMPT = """
你是代码知识库问答助手（Ask 模式）。用户正在分析一个已索引的代码库。

要求：
1. 快速、准确地回答用户问题
2. 必须使用 codebase 工具检索相关代码后再回答
3. 回答中必须包含源码定位，格式为 `文件路径:行号`
4. 涉及调用关系时，用 ```mermaid 代码块输出调用链/流程图
5. 不要规划任务或修改代码，仅做问答与分析
"""


class CodeAskAgent(BaseAgent):
    name: str = "code_ask"
    _system_prompt: str = SYSTEM_PROMPT + CODE_ASK_PROMPT
    _format: str = "text"

    def _should_emit_deltas(self) -> bool:
        return True


class CodeAskFlow(BaseFlow):
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            agent_config: AgentConfig,
            session_id: str,
            json_parser: JSONParser,
            browser: Browser,
            sandbox: Sandbox,
            search_engine: SearchEngine,
            mcp_tool: MCPTool,
            a2a_tool: A2ATool,
            extra_tools: Optional[List[BaseTool]] = None,
            model_id: Optional[str] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_id = session_id
        self.status = FlowStatus.EXECUTING
        tools = ToolRegistry.build_default_tools(
            sandbox=sandbox,
            browser=browser,
            search_engine=search_engine,
            llm=llm,
            mcp_tool=mcp_tool,
            a2a_tool=a2a_tool,
            extra_tools=extra_tools or [],
        )
        self._agent = CodeAskAgent(
            uow_factory=uow_factory,
            llm=llm,
            agent_config=agent_config,
            session_id=session_id,
            json_parser=json_parser,
            tools=tools,
            model_id=model_id,
        )

    @property
    def done(self) -> bool:
        return self.status == FlowStatus.COMPLETED

    async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        try:
            async for event in self._agent.invoke(message):
                yield event
            self.status = FlowStatus.COMPLETED
            yield DoneEvent()
        except Exception as exc:
            logger.exception("CodeAskFlow 失败: %s", exc)
            yield ErrorEvent(error=str(exc))
            self.status = FlowStatus.COMPLETED
