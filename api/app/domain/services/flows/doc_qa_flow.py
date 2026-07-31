#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight RAG flow for enterprise document KB Ask mode."""
import asyncio
import logging
from typing import AsyncGenerator, Callable, List, Optional

from app.application.services.config_provider import get_runtime_config
from app.domain.external.browser import Browser
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.observability import ObservabilityPort
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, WaitEvent
from app.domain.models.message import Message
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.loader import compose_system_prompt, detect_locale_from_text, load_prompts
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.tool_registry import ToolRegistry
from .base import BaseFlow, FlowStatus

logger = logging.getLogger(__name__)


class DocQAAgent(BaseAgent):
    name: str = "doc_qa"
    _system_prompt: str = ""
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
        self._reset_outcome()
        tools = ToolRegistry.build_ask_tools(
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
        self._reset_outcome()
        try:
            prompts = load_prompts(detect_locale_from_text(message.message))
            runtime = get_runtime_config()
            self._agent.set_locale(prompts.locale)
            self._agent._system_prompt = compose_system_prompt(
                prompts,
                prompts.flows.DOC_QA_PROMPT,
                sandbox_runtime=runtime.sandbox_runtime,
                writing_style=runtime.prompt.writing_style,
            )
            async for event in self._agent.invoke(
                message.message,
                vision_attachments=message.vision_attachments or None,
            ):
                if isinstance(event, ErrorEvent):
                    self._mark_failed(event.error, event.code)
                elif isinstance(event, WaitEvent):
                    self._mark_waiting()
                yield event
                if isinstance(event, WaitEvent):
                    self.status = FlowStatus.COMPLETED
                    return
            self.status = FlowStatus.COMPLETED
            if self.outcome.error and self.outcome.error.code == "FLOW_OUTCOME_UNSET":
                self._mark_succeeded()
            yield DoneEvent()
        except asyncio.CancelledError:
            self._mark_cancelled()
            raise
        except Exception as exc:
            from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError

            if isinstance(exc, ModelUnavailableError):
                self._mark_failed(str(exc), exc.error_code)
                raise
            logger.exception("DocQAFlow 失败: %s", exc)
            self._mark_failed(str(exc), getattr(exc, "error_code", None))
            yield ErrorEvent(error=str(exc))
            self.status = FlowStatus.COMPLETED
