#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory helpers for isolated sub-agent execution."""
import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.observability import ObservabilityPort
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import MessageEvent
from app.application.services.config_provider import get_runtime_config
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.subagent import SubAgentAgent
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.subagent import SubAgentTool
from app.domain.services.prompts.loader import compose_system_prompt, load_prompts, resolve_writing_style
from app.domain.services.tools.tool_names import is_tool_allowed, normalize_allowed_tool_names
from app.domain.services.tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def build_subagent_tool(
        *,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
        llm: LLM,
        agent_config: AgentConfig,
        json_parser: JSONParser,
        browser: Browser,
        sandbox: Sandbox,
        search_engine: SearchEngine,
        mcp_tool: MCPTool,
        a2a_tool: A2ATool,
        observability_port: ObservabilityPort,
        runtime_settings: AgentRuntimeSettings,
        extra_tools: Optional[List[BaseTool]] = None,
        skill_prompt: str = "",
        long_term_memory_block: str = "",
        allowed_tool_names: Optional[List[str]] = None,
        model_id: Optional[str] = None,
        file_storage: Optional[FileStorage] = None,
        stateful_tool_lock: Optional[asyncio.Lock] = None,
        writing_style_override: Optional[str] = None,
        override_base_rules: bool = False,
        prompt_locale: str = "en",
) -> SubAgentTool:
    """Build SubAgentTool with a closure that spawns isolated SubAgentAgent instances."""
    base_extra = [t for t in (extra_tools or []) if not isinstance(t, SubAgentTool)]
    lock = stateful_tool_lock or asyncio.Lock()
    parent_allowed = normalize_allowed_tool_names(allowed_tool_names)

    async def _run_subagent(
            *,
            goal: str,
            agent_name: str,
            allowed_tools: Optional[List[str]] = None,
    ) -> str:
        sub_allowed = normalize_allowed_tool_names(allowed_tools) if allowed_tools else parent_allowed
        if parent_allowed is not None and sub_allowed is not None:
            sub_allowed = [n for n in sub_allowed if is_tool_allowed(n, parent_allowed)]
        tools = ToolRegistry.build_default_tools(
            sandbox=sandbox,
            browser=browser,
            search_engine=search_engine,
            llm=llm,
            mcp_tool=mcp_tool,
            a2a_tool=a2a_tool,
            extra_tools=base_extra,
        )
        sub_iterations = min(agent_config.subagent_max_iterations, agent_config.max_iterations)
        sub_config = agent_config.model_copy(update={"max_iterations": sub_iterations})
        prompts = load_prompts(prompt_locale)
        runtime = get_runtime_config()
        style = resolve_writing_style(
            writing_style_override,
            override_base_rules,
            runtime.prompt.writing_style,
        )
        subagent_extra = prompts.internal.SUBAGENT_SYSTEM_PROMPT
        system_prompt = compose_system_prompt(
            prompts,
            subagent_extra,
            sandbox_runtime=runtime.sandbox_runtime,
            writing_style=style,
        )
        agent = SubAgentAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=sub_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            skill_prompt=skill_prompt,
            long_term_memory_block=long_term_memory_block,
            allowed_tool_names=sub_allowed,
            model_id=model_id,
            file_storage=file_storage,
            observability_port=observability_port,
            runtime_settings=runtime_settings,
            stateful_tool_lock=lock,
            writing_style_override=writing_style_override,
            override_base_rules=override_base_rules,
            prompt_locale=prompt_locale,
        )
        agent._system_prompt = system_prompt
        agent.set_locale(prompt_locale)
        agent.name = agent_name
        summary = ""
        async for event in agent.invoke(goal, format=None, emit_deltas=False):
            if isinstance(event, MessageEvent) and event.message:
                summary = event.message
        if not summary:
            raise RuntimeError("子 Agent 未返回有效摘要")
        return summary

    return SubAgentTool(
        run_subagent=_run_subagent,
        max_concurrency=agent_config.subagent_max_concurrency,
    )
