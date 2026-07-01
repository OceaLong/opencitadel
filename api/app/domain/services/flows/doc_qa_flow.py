#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight RAG flow for enterprise document KB Ask mode."""
import logging
from typing import AsyncGenerator, Callable, List, Optional

from app.domain.external.browser import Browser
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.observability import ObservabilityPort
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent
from app.domain.models.message import Message
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.tool_registry import ToolRegistry
from .base import BaseFlow, FlowStatus

logger = logging.getLogger(__name__)

DOC_QA_PROMPT = """
你是企业文档知识库问答助手（Ask 模式）。用户正在询问一个已索引的企业文档知识库。

要求：
1. 必须先使用 knowledge_base 工具检索相关文档，再回答问题
2. 回答中的事实性结论必须带来源引用，优先复用工具返回的 `kbdoc://` Markdown 链接
3. 如果检索不到依据，明确说明“知识库中没有找到可靠依据”，不要编造
4. 只做问答、总结、对比与解释，不规划改动、不执行文件或系统操作
"""


class DocQAAgent(BaseAgent):
    name: str = "doc_qa"
    _system_prompt: str = SYSTEM_PROMPT + DOC_QA_PROMPT
    _format: str = "text"

    def _should_emit_deltas(self) -> bool:
        return True


class DocQAFlow(BaseFlow):
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
            observability_port: ObservabilityPort,
            runtime_settings: AgentRuntimeSettings,
            extra_tools: Optional[List[BaseTool]] = None,
            model_id: Optional[str] = None,
    ) -> None:
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
        self._agent = DocQAAgent(
            uow_factory=uow_factory,
            llm=llm,
            agent_config=agent_config,
            session_id=session_id,
            json_parser=json_parser,
            tools=tools,
            model_id=model_id,
            observability_port=observability_port,
            runtime_settings=runtime_settings,
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
            logger.exception("DocQAFlow 失败: %s", exc)
            yield ErrorEvent(error=str(exc))
            self.status = FlowStatus.COMPLETED
