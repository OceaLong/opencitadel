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
from app.domain.models.event import ToolEvent, ToolEventStatus, ErrorEvent, MessageEvent, BaseEvent
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.tool_names import normalize_allowed_tool_names, normalize_tool_name

logger = logging.getLogger(__name__)


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
    def _messages_for_llm(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """发送给 LLM 前移除内部字段，避免 OpenAI 兼容接口校验失败。"""
        sanitized: List[Dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "tool":
                sanitized.append({
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": message.get("content"),
                })
            else:
                sanitized.append(message)
        return sanitized

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

    async def _invoke_llm(self, messages: List[Dict[str, Any]], format: Optional[str] = None) -> Dict[str, Any]:
        """调用语言模型并处理记忆内容"""
        # 1.将消息添加到记忆中
        await self._add_to_memory(messages)

        # 2.组装语言模型的响应格式
        available_tools = self._get_available_tools()
        effective_format = format
        if effective_format == "json_object" and available_tools:
            logger.debug("工具可用时跳过 json_object response_format，以兼容 tool_calls")
            effective_format = None
        response_format = {"type": effective_format} if effective_format else None

        # 3.循环向LLM发起提问直到最大重试次数
        error = "调用语言模型发生错误"
        for _ in range(self._agent_config.max_retries):
            try:
                # 4.调用语言模型获取响应内容
                message = await self._llm.invoke(
                    messages=self._messages_for_llm(self._memory.get_messages()),
                    tools=available_tools,
                    response_format=response_format,
                    tool_choice=self._tool_choice,
                )

                # 5.处理AI响应内容避免空回复
                if message.get("role") == "assistant":
                    content = message.get("content")
                    tool_calls = message.get("tool_calls")
                    reasoning_content = message.get("reasoning_content")
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

                    # 6.取出非空消息并处理工具调用(兼容DeepSeek思考模型的写法)
                    filtered_message = {"role": "assistant", "content": content}
                    if reasoning_content:
                        filtered_message["reasoning_content"] = reasoning_content
                    if message.get("tool_calls"):
                        # 7.取出工具调用的数据，限制LLM一次只能调用工具
                        filtered_message["tool_calls"] = message.get("tool_calls")[:1]
                else:
                    # 8.非AI消息则记录日志并存储message
                    logger.warning(f"LLM响应内容无法确认消息角色: {message.get('role')}")
                    filtered_message = message

                # 9.将消息添加到记忆中
                await self._add_to_memory([filtered_message])
                return filtered_message
            except Exception as e:
                # 10.记录日志并睡眠指定的时间
                error_msg = _format_agent_error(e)
                logger.error(
                    f"调用语言模型发生错误: {error_msg}",
                    exc_info=not isinstance(e, AppException),
                )
                error = error_msg
                await asyncio.sleep(self._retry_interval)
                continue

        # 11.所有重试均已耗尽仍未获得有效响应，抛出异常避免返回None
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

    async def invoke(self, query: str, format: Optional[str] = None) -> AsyncGenerator[BaseEvent, None]:
        """传递消息+响应格式调用程序生成异步迭代内容"""
        # 1.需要判断下是否传递了format
        format = format if format else self._format

        # 2.调用语言模型获取响应内容
        message = await self._invoke_llm(
            [{"role": "user", "content": query}],
            format,
        )

        # 3.循环遍历直到最大迭代次数
        for _ in range(self._agent_config.max_iterations):
            # 4.如果LLM响应为空或无工具调用则表示LLM生成了文本回答，这时候就是最终答案
            if not message or not message.get("tool_calls"):
                break

            # 5.循环遍历工具参数并执行
            tool_messages = []
            for tool_call in message["tool_calls"]:
                if not tool_call.get("function"):
                    continue

                # 6.取出调用工具id、名字、参数信息
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
                    yield ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name="unknown",
                        function_name=function_name,
                        function_args=function_args if isinstance(function_args, dict) else {},
                        function_result=result,
                        status=ToolEventStatus.CALLED,
                    )
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "_function_name": function_name,
                        "content": result.model_dump_json(),
                    })
                    continue

                # 8.返回工具即将调用事件，其中tool_content比较特殊，需要在具体业务中进行实现，这里留空即可
                yield ToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool.name,
                    function_name=function_name,
                    function_args=function_args,
                    status=ToolEventStatus.CALLING,
                )

                # 9.调用工具并获取结果
                result = await self._invoke_tool(tool, function_name, function_args)

                # 10.返回工具调用结果，其中tool_content比较特殊，需要在业务中进行实现
                yield ToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool.name,
                    function_name=function_name,
                    function_args=function_args,
                    function_result=result,
                    status=ToolEventStatus.CALLED,
                )

                # 11.组装工具响应
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "_function_name": function_name,
                    "content": result.model_dump_json(),
                })

            # 12.所有工具都执行完成后，调用LLM获取汇总消息二次提供
            message = await self._invoke_llm(tool_messages, format=None)
        else:
            # 13.超过最大迭代次数后，则抛出错误
            yield ErrorEvent(error=f"Agent迭代超过最大迭代次数: {self._agent_config.max_iterations}, 任务处理失败")

        # 14.在指定步骤内完成了迭代则返回消息事件
        if message and message.get("content") is not None:
            yield MessageEvent(message=message["content"])
        else:
            yield ErrorEvent(error="Agent未能生成有效回复内容")
