#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import uuid
from abc import ABC
from typing import Optional, List, AsyncGenerator, Dict, Any, Callable

from app.application.errors.exceptions import AppException
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import (
    ToolEvent, ToolEventStatus, ErrorEvent, MessageEvent, BaseEvent,
    MessageDeltaEvent, ReasoningDeltaEvent, ToolArgsDeltaEvent,
)
from app.domain.models.memory import Memory
from app.domain.models.message import Message, VisionAttachment
from app.domain.models.tool_result import ToolResult
from app.domain.services import vision_service
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.tool_names import normalize_allowed_tool_names, normalize_tool_name

logger = logging.getLogger(__name__)

BROWSER_VISION_TOOLS = frozenset({"browser_screenshot"})
STATEFUL_TOOL_NAMES = frozenset({"browser", "shell"})


def _format_agent_error(error: Exception) -> str:
    """提取可读错误信息，兼容 AppException 空 str(e) 问题。"""
    msg = getattr(error, "msg", None)
    if msg:
        return str(msg)
    text = str(error)
    return text if text else repr(error)


class BaseAgent(ABC):
    """基础Agent智能体"""
    name: str = ""  # 智能体名字
    _system_prompt: str = ""  # 系统预设prompt
    _format: Optional[str] = None  # Agent的响应格式
    _retry_interval: float = 1.0  # 重试间隔
    _tool_choice: Optional[str] = None  # 强制选择工具

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,  # 会话id
            agent_config: AgentConfig,  # Agent配置
            llm: LLM,  # 语言模型协议
            json_parser: JSONParser,  # JSON输出解析器
            tools: List[BaseTool],  # 工具列表
            skill_prompt: str = "",
            long_term_memory_block: str = "",
            allowed_tool_names: Optional[List[str]] = None,
    ) -> None:
        """构造函数，完成Agent的初始化"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self._agent_config = agent_config
        self._llm = llm
        self._memory: Optional[Memory] = None
        self._json_parser = json_parser
        self._tools = tools
        self._skill_prompt = skill_prompt
        self._long_term_memory_block = long_term_memory_block
        self._allowed_tool_names = normalize_allowed_tool_names(allowed_tool_names)
        self._stateful_tool_lock = asyncio.Lock()
        self._pending_delta_events: List[BaseEvent] = []

    def _collect_registered_tool_names(self) -> List[str]:
        names: List[str] = []
        for tool in self._tools:
            for schema in tool.get_tools():
                name = schema.get("function", {}).get("name", "")
                if name:
                    names.append(name)
        return names

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """获取Agent所有可用的工具列表参数声明/Schema"""
        registered_names = self._collect_registered_tool_names()
        available_tools = []
        for tool in self._tools:
            for schema in tool.get_tools():
                name = schema.get("function", {}).get("name", "")
                if self._allowed_tool_names and name not in self._allowed_tool_names:
                    continue
                available_tools.append(schema)

        if self._allowed_tool_names and registered_names:
            if not available_tools:
                logger.warning(
                    "Skill 工具白名单过滤后无可用工具: session=%s allowed_tools=%s registered_tools=%s",
                    self._session_id,
                    self._allowed_tool_names,
                    registered_names,
                )
            elif len(available_tools) < len(self._allowed_tool_names):
                available_names = [s.get("function", {}).get("name", "") for s in available_tools]
                logger.debug(
                    "Skill 工具白名单已过滤: session=%s allowed=%s available=%s",
                    self._session_id,
                    self._allowed_tool_names,
                    available_names,
                )
        return available_tools

    @classmethod
    def _messages_for_llm(cls, messages: List[Dict[str, Any]], llm: Optional[LLM] = None) -> List[Dict[str, Any]]:
        """发送给 LLM 前移除内部字段，并将 image_ref 还原为 provider 可识别格式。"""
        sanitized: List[Dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "tool":
                sanitized.append({
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": message.get("content"),
                })
            else:
                cleaned = {k: v for k, v in message.items() if not k.startswith("_")}
                sanitized.append(cleaned)
        return vision_service.inflate_messages_for_llm(sanitized, llm)

    def _build_browser_tool_payload(
            self,
            function_name: str,
            result: ToolResult,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """构建浏览器截图工具返回给 LLM 的文本与可选截图 user 消息。"""
        return vision_service.build_screenshot_messages(
            function_name,
            result.data or {},
            self._llm,
        )

    def _resolve_tool(self, function_name: str) -> BaseTool:
        """获取工具包，兼容旧工具名。"""
        normalized_name = normalize_tool_name(function_name)
        for tool in self._tools:
            if tool.has_tool(normalized_name):
                return tool
        raise ValueError(f"未知工具: {function_name}")

    async def _ensure_memory(self) -> None:
        """确保智能体记忆是存在的"""
        if self._memory is None:
            async with self._uow:
                self._memory = await self._uow.session.get_memory(self._session_id, self.name)

    async def _invoke_llm(
            self,
            messages: List[Dict[str, Any]],
            format: Optional[str] = None,
            stream_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用语言模型并处理记忆内容，支持流式 delta 事件。"""
        await self._add_to_memory(messages)

        available_tools = self._get_available_tools()
        effective_format = format
        if effective_format == "json_object" and available_tools:
            logger.debug("工具可用时跳过 json_object response_format，以兼容 tool_calls")
            effective_format = None
        response_format = {"type": effective_format} if effective_format else None

        error = "调用语言模型发生错误"
        for _ in range(self._agent_config.max_retries):
            try:
                stream_id = stream_id or str(uuid.uuid4())
                aggregated: Dict[str, Any] = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "",
                    "tool_calls": [],
                }
                tool_call_acc: Dict[int, Dict[str, Any]] = {}
                delta_events: List[BaseEvent] = []

                async for delta in self._llm.stream_invoke(
                        messages=self._messages_for_llm(self._memory.get_messages(), self._llm),
                        tools=available_tools,
                        response_format=response_format,
                        tool_choice=self._tool_choice,
                ):
                    if content := delta.get("content"):
                        aggregated["content"] += content
                        delta_events.append(MessageDeltaEvent(stream_id=stream_id, delta=content))
                    if reasoning := delta.get("reasoning_content"):
                        aggregated["reasoning_content"] += reasoning
                        delta_events.append(ReasoningDeltaEvent(stream_id=stream_id, delta=reasoning))
                    for tc_delta in delta.get("tool_calls") or []:
                        idx = tc_delta.get("index", 0)
                        acc = tool_call_acc.setdefault(idx, {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            acc["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["function"]["arguments"] += fn["arguments"]
                            delta_events.append(ToolArgsDeltaEvent(
                                stream_id=stream_id,
                                tool_call_id=acc["id"] or f"pending-{idx}",
                                tool_name=acc["function"]["name"],
                                delta=fn["arguments"],
                            ))

                if tool_call_acc:
                    aggregated["tool_calls"] = [
                        tool_call_acc[i] for i in sorted(tool_call_acc.keys())
                    ]

                self._pending_delta_events = delta_events

                content = aggregated.get("content")
                tool_calls = aggregated.get("tool_calls")
                reasoning_content = aggregated.get("reasoning_content")
                if not content and not tool_calls and not reasoning_content:
                    logger.warning("LLM回复了空内容，执行重试")
                    await self._add_to_memory([
                        {"role": "assistant", "content": ""},
                        {"role": "user", "content": "AI无响应内容，请继续。"}
                    ])
                    await asyncio.sleep(self._retry_interval)
                    continue
                if not content and not tool_calls and reasoning_content:
                    logger.warning(
                        "LLM仅返回reasoning_content，未返回content/tool_calls，"
                        "请检查思考模式参数或模型兼容性"
                    )

                filtered_message = {"role": "assistant", "content": content or None}
                if reasoning_content:
                    filtered_message["reasoning_content"] = reasoning_content
                if tool_calls:
                    filtered_message["tool_calls"] = tool_calls
                    filtered_message["stream_id"] = stream_id

                await self._add_to_memory([filtered_message])
                return filtered_message
            except Exception as e:
                error_msg = _format_agent_error(e)
                logger.error(
                    f"调用语言模型发生错误: {error_msg}",
                    exc_info=not isinstance(e, AppException),
                )
                error = error_msg
                await asyncio.sleep(self._retry_interval)
                continue

        raise RuntimeError(f"调用语言模型失败, 已达到最大重试次数({self._agent_config.max_retries}): {error}")

    async def _invoke_tool(self, tool: BaseTool, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """传递工具包+工具名字+对应参数调用指定工具"""
        # 1.执行循环调用工具获取结果
        err = ""
        for _ in range(self._agent_config.max_retries):
            try:
                return await tool.invoke(tool_name, **arguments)
            except Exception as e:
                err = str(e)
                logger.exception(f"调用工具[{tool_name}]出错, 错误: {str(e)}")
                await asyncio.sleep(self._retry_interval)
                continue

        # 2.循环最大重试次数后没有结果则将错误作为工具的执行结果，让LLM自行处理
        return ToolResult(success=False, message=err)

    async def _add_to_memory(self, messages: List[Dict[str, Any]]) -> None:
        """将对应的信息添加到记忆中"""
        # 1.先检查确保记忆是存在的
        await self._ensure_memory()

        # 2.检查记忆的消息列表是否为空，如果是空则需要添加预设prompt作为初始记忆
        if self._memory.empty:
            system_content = self._system_prompt
            if self._skill_prompt:
                system_content += f"\n\n--- Skill Instructions ---\n{self._skill_prompt}"
            if self._long_term_memory_block:
                system_content += f"\n\n{self._long_term_memory_block}"
            self._memory.add_message({
                "role": "system", "content": system_content,
            })

        # 3.将正常消息添加到记忆中
        self._memory.add_messages(messages)

        # 4.将记忆持久化到数据仓库中
        async with self._uow:
            await self._uow.session.save_memory(self._session_id, self.name, self._memory)

    async def compact_memory(self) -> None:
        """压缩Agent的记忆"""
        await self._ensure_memory()
        self._memory.compact()
        async with self._uow:
            await self._uow.session.save_memory(self._session_id, self.name, self._memory)

    async def roll_back(self, message: Message) -> None:
        """Agent的状态回滚，该函数用于确保Agent的消息列表状态是正确，用于发送新消息、暂停/停止任务、通知用户"""
        # 1.取出记忆中的最后一条消息，检查是否是工具调用
        await self._ensure_memory()
        last_message = self._memory.get_last_message()
        if (
                not last_message or
                not last_message.get("tool_calls") or
                len(last_message.get("tool_calls")) == 0
        ):
            return

        # 2.取出消息中的工具调用参数
        tool_call = last_message.get("tool_calls")[0]

        # 3.提取工具名字、id
        function_name = tool_call.get("function", {}).get("name")
        tool_call_id = tool_call.get("id")

        # 4.判断下当前的工具是不是通知用户(message_ask_user)
        if function_name == "message_ask_user":
            self._memory.add_message({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "_function_name": function_name,
                "content": message.model_dump_json(),
            })
        else:
            # 5.否则直接删除最后一条消息
            self._memory.roll_back()

        # 6.将记忆持久化
        async with self._uow:
            await self._uow.session.save_memory(self._session_id, self.name, self._memory)

    async def invoke(
            self,
            query: str,
            format: Optional[str] = None,
            vision_attachments: Optional[List[VisionAttachment]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """传递消息+响应格式调用程序生成异步迭代内容"""
        # 1.需要判断下是否传递了format
        format = format if format else self._format

        # 2.调用语言模型获取响应内容
        user_message = vision_service.build_user_message(
            query,
            vision_attachments,
            llm=self._llm,
        )
        message = await self._invoke_llm([user_message], format)
        for delta_event in getattr(self, "_pending_delta_events", []):
            yield delta_event
        self._pending_delta_events = []

        # 3.循环遍历直到最大迭代次数
        for _ in range(self._agent_config.max_iterations):
            # 4.如果LLM响应为空或无工具调用则表示LLM生成了文本回答，这时候就是最终答案
            if not message or not message.get("tool_calls"):
                break

            tool_calls = message.get("tool_calls") or []
            tool_messages: List[Dict[str, Any]] = []

            async def _run_tool_call(tool_call: Dict[str, Any]) -> tuple[List[BaseEvent], List[Dict[str, Any]]]:
                events: List[BaseEvent] = []
                msgs: List[Dict[str, Any]] = []
                if not tool_call.get("function"):
                    return events, msgs

                tool_call_id = tool_call["id"] or str(uuid.uuid4())
                function_name = normalize_tool_name(tool_call["function"]["name"])
                raw_arguments = tool_call["function"]["arguments"]
                if isinstance(raw_arguments, dict):
                    function_args = raw_arguments
                else:
                    function_args = await self._json_parser.invoke(raw_arguments)

                try:
                    tool = self._resolve_tool(function_name)
                except ValueError as exc:
                    logger.warning(
                        "会话[%s] 调用未知工具[%s]: %s",
                        self._session_id,
                        function_name,
                        exc,
                    )
                    result = ToolResult(success=False, message=str(exc))
                    events.append(ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name="unknown",
                        function_name=function_name,
                        function_args=function_args if isinstance(function_args, dict) else {},
                        function_result=result,
                        status=ToolEventStatus.CALLED,
                    ))
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "_function_name": function_name,
                        "content": result.model_dump_json(),
                    })
                    return events, msgs

                async def _execute() -> tuple[List[BaseEvent], List[Dict[str, Any]]]:
                    inner_events: List[BaseEvent] = []
                    inner_msgs: List[Dict[str, Any]] = []
                    inner_events.append(ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name=tool.name,
                        function_name=function_name,
                        function_args=function_args,
                        status=ToolEventStatus.CALLING,
                    ))
                    result = await self._invoke_tool(tool, function_name, function_args)
                    inner_events.append(ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name=tool.name,
                        function_name=function_name,
                        function_args=function_args,
                        function_result=result,
                        status=ToolEventStatus.CALLED,
                    ))
                    extra_messages: List[Dict[str, Any]] = []
                    if (
                            function_name in BROWSER_VISION_TOOLS
                            and vision_service.vision_enabled(self._llm)
                            and result.success
                    ):
                        tool_content, extra_messages = self._build_browser_tool_payload(
                            function_name,
                            result,
                        )
                    else:
                        tool_content = result.model_dump_json()
                    inner_msgs.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "_function_name": function_name,
                        "content": tool_content,
                    })
                    inner_msgs.extend(extra_messages)
                    return inner_events, inner_msgs

                if tool.name in STATEFUL_TOOL_NAMES:
                    async with self._stateful_tool_lock:
                        return await _execute()
                return await _execute()

            results = await asyncio.gather(*[_run_tool_call(tc) for tc in tool_calls])
            for events, msgs in results:
                for event in events:
                    yield event
                tool_messages.extend(msgs)

            message = await self._invoke_llm(tool_messages, format=None)
            for delta_event in getattr(self, "_pending_delta_events", []):
                yield delta_event
            self._pending_delta_events = []
        else:
            # 13.超过最大迭代次数后，则抛出错误
            yield ErrorEvent(error=f"Agent迭代超过最大迭代次数: {self._agent_config.max_iterations}, 任务处理失败")

        # 14.在指定步骤内完成了迭代则返回消息事件
        if message and message.get("content") is not None:
            yield MessageEvent(
                message=message["content"],
                stream_id=message.get("stream_id"),
            )
        else:
            yield ErrorEvent(error="Agent未能生成有效回复内容")
