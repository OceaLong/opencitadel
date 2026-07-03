#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import time
import uuid
from hashlib import sha256
from abc import ABC
from typing import Optional, List, AsyncGenerator, Dict, Any, Callable, Type
from urllib.parse import urlparse

from app.domain.external.observability import ObservabilityPort
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import (
    ToolEvent, ToolEventStatus, ErrorEvent, MessageEvent, BaseEvent,
    MessageDeltaEvent, ReasoningDeltaEvent, ToolArgsDeltaEvent, UsageEvent,
    ApprovalEvent, WaitEvent,
)
from app.domain.models.memory import Memory
from app.domain.models.message import Message, VisionAttachment
from app.domain.models.tool_result import ToolResult
from app.domain.services import vision_service
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.token_accountant import TokenAccountant
from app.domain.services.agents.retry_budget import LLMRetryBudget, RetryBudgetExceeded
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.tool_names import (
    is_tool_allowed,
    normalize_allowed_tool_names,
    normalize_tool_name,
)
from app.domain.utils.vision_tokens import estimate_messages_tokens
from app.domain.models.error_codes import MODEL_UNAVAILABLE, TASK_INFRA_FAILED
from app.domain.utils.llm_retry import is_retriable_llm_error
from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError
from app.application.services.config_provider import get_runtime_config
from app.domain.utils.hitl import (
    TOOL_APPROVAL_PHASE,
    merge_pending_metadata,
    tool_matches_risk_list,
    domain_in_whitelist,
    matches_critical_action,
    resolve_gate_profile_settings,
)
from app.domain.utils.audit_redaction import redact_tool_args, summarize_tool_result
from app.domain.models.audit_log import AuditLog
from app.domain.services.agent.sandbox_lifecycle import SandboxLifecycleCoordinator
from app.infrastructure.external.llm.structured_output import schema_payload
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MEMORY_SUMMARY_PROMPT = """请将以下 Agent 对话历史压缩为简洁摘要，保留：
- 已完成的关键操作与结论
- 重要文件路径、数据、错误信息
- 用户目标与当前进度

只输出摘要正文，不要 JSON。使用与历史相同的语言。

历史消息:
{history}
"""

BROWSER_VISION_TOOLS = frozenset({"browser_screenshot"})
STATEFUL_TOOL_NAMES = frozenset({"browser", "shell"})

# Tools whose large results may be offloaded to sandbox filesystem cache.
_OFFLOAD_ELIGIBLE_TOOLS = frozenset({
    "search_web",
    "shell_execute",
    "shell_read_output",
    "browser_console_view",
    "browser_view",
})


def _format_agent_error(error: Exception) -> str:
    """提取可读错误信息，兼容自定义异常空 str(e) 问题。"""
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
            observability_port: ObservabilityPort,
            runtime_settings: AgentRuntimeSettings,
            skill_prompt: str = "",
            long_term_memory_block: str = "",
            allowed_tool_names: Optional[List[str]] = None,
            model_id: Optional[str] = None,
            file_storage: Optional[FileStorage] = None,
            stateful_tool_lock: Optional[asyncio.Lock] = None,
            sandbox_lifecycle: Optional[SandboxLifecycleCoordinator] = None,
    ) -> None:
        """构造函数，完成Agent的初始化"""
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._agent_config = agent_config
        self._llm = llm
        self._model_id = model_id
        self._memory: Optional[Memory] = None
        self._json_parser = json_parser
        self._tools = tools
        self._skill_prompt = skill_prompt
        self._long_term_memory_block = long_term_memory_block
        self._allowed_tool_names = normalize_allowed_tool_names(allowed_tool_names)
        self._file_storage = file_storage
        self._stateful_tool_lock = stateful_tool_lock or asyncio.Lock()
        self._sandbox_lifecycle = sandbox_lifecycle
        self._pending_usage_event: Optional[UsageEvent] = None
        self._last_llm_message: Optional[Dict[str, Any]] = None
        self._current_step: str = "default"
        self._last_prompt_tokens: int = 0
        self._runtime_settings = runtime_settings
        self._token_accountant = TokenAccountant(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_name=self.name,
            model_name=llm.model_name,
            model_id=model_id,
            observability_port=observability_port,
        )
        self._tool_index: Dict[str, BaseTool] = {}
        self._all_tool_schemas: List[Dict[str, Any]] = []
        self._cached_available_tools: Optional[List[Dict[str, Any]]] = None
        self._tool_cache_signature: Optional[str] = None
        resilience_cfg = get_runtime_config().model_resilience
        self._retry_budget = LLMRetryBudget.create(
            max_calls=self._agent_config.max_retries * resilience_cfg.max_attempts_per_call + 2,
            max_seconds=resilience_cfg.max_call_budget_seconds,
        )

    def _tool_cache_signature_value(self) -> str:
        names = sorted(
            schema.get("function", {}).get("name", "")
            for tool in self._tools
            for schema in tool.get_tools()
        )
        return sha256("|".join(names).encode("utf-8")).hexdigest()

    def _rebuild_tool_cache(self) -> None:
        self._tool_index = {}
        self._all_tool_schemas = []
        for tool in self._tools:
            for schema in tool.get_tools():
                name = schema.get("function", {}).get("name", "")
                if not name:
                    continue
                self._tool_index[name] = tool
                self._all_tool_schemas.append(schema)
        self._all_tool_schemas.sort(key=lambda item: item.get("function", {}).get("name", ""))
        self._cached_available_tools = None
        self._tool_cache_signature = self._tool_cache_signature_value()

    def _ensure_tool_cache(self) -> None:
        signature = self._tool_cache_signature_value()
        if self._tool_cache_signature != signature:
            self._rebuild_tool_cache()

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
        self._ensure_tool_cache()
        if self._cached_available_tools is not None:
            return self._cached_available_tools

        registered_names = [s.get("function", {}).get("name", "") for s in self._all_tool_schemas]
        available_tools = []
        for schema in self._all_tool_schemas:
            name = schema.get("function", {}).get("name", "")
            if self._allowed_tool_names is not None and not is_tool_allowed(name, self._allowed_tool_names):
                continue
            available_tools.append(schema)

        if self._allowed_tool_names is not None and registered_names:
            if not available_tools:
                logger.warning(
                    "Skill 工具白名单过滤后无可用工具: session=%s allowed_tools=%s registered_tools=%s",
                    self._session_id,
                    self._allowed_tool_names,
                    registered_names,
                )
            else:
                available_names = [s.get("function", {}).get("name", "") for s in available_tools]
                logger.debug(
                    "Skill 工具白名单已过滤: session=%s allowed=%s available=%s",
                    self._session_id,
                    self._allowed_tool_names,
                    available_names,
                )
        self._cached_available_tools = available_tools
        return available_tools

    @classmethod
    @staticmethod
    def _messages_for_llm(
            messages: List[Dict[str, Any]],
            llm: Optional[LLM] = None,
            *,
            strip_images: bool = False,
    ) -> List[Dict[str, Any]]:
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
        inflated = vision_service.inflate_messages_for_llm(sanitized, llm)
        if strip_images or (llm is not None and not vision_service.vision_enabled(llm)):
            return vision_service.strip_images_for_tool_call(inflated)
        return inflated

    async def _build_browser_tool_payload(
            self,
            function_name: str,
            result: ToolResult,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """构建浏览器截图工具返回给 LLM 的文本与可选截图 user 消息。"""
        return await vision_service.build_screenshot_messages(
            function_name,
            result.data or {},
            self._llm,
            file_storage=self._file_storage,
        )

    def _resolve_tool(self, function_name: str) -> BaseTool:
        """获取工具包，兼容旧工具名。"""
        self._ensure_tool_cache()
        normalized_name = normalize_tool_name(function_name)
        tool = self._tool_index.get(normalized_name)
        if tool is not None:
            return tool
        for candidate in self._tools:
            if candidate.has_tool(normalized_name):
                self._tool_index[normalized_name] = candidate
                return candidate
        raise ValueError(f"未知工具: {function_name}")

    @staticmethod
    def _truncate_tool_result(
            result: ToolResult,
            max_chars: int,
            *,
            function_name: Optional[str] = None,
            function_args: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """截断过大的工具结果，避免撑爆 LLM 上下文。"""
        if max_chars <= 0:
            return result
        serialized = result.model_dump_json()
        if len(serialized) <= max_chars:
            return result
        from app.domain.models.memory import _format_truncation_call_hint
        hint = _format_truncation_call_hint(function_name, function_args or {})
        notice = (
            f"\n\n{hint}[结果已截断: 原始长度 {len(serialized)} 字符，保留前 {max_chars} 字符。"
            "如需完整内容请缩小查询范围或使用 read_file 等工具分页获取。]"
        )
        budget = max(0, max_chars - len(notice))
        truncated_payload = serialized[:budget] + notice
        return ToolResult(
            success=result.success,
            message=(result.message or "") + notice if result.message else notice.strip(),
            data=truncated_payload,
        )

    async def _offload_large_result(
            self,
            tool_call_id: str,
            function_name: str,
            result: ToolResult,
    ) -> ToolResult:
        memory_cfg = self._runtime_settings.memory
        if not memory_cfg.tool_output_offload_enabled:
            return result
        if function_name not in _OFFLOAD_ELIGIBLE_TOOLS:
            return result
        serialized = result.model_dump_json()
        threshold = memory_cfg.tool_output_offload_threshold_chars
        if len(serialized) <= threshold:
            return result
        file_tool = self._tool_index.get("write_file")
        if file_tool is None:
            self._ensure_tool_cache()
            file_tool = self._tool_index.get("write_file")
        if file_tool is None:
            return result
        cache_path = f"/home/ubuntu/.opencitadel_cache/{tool_call_id}.json"
        try:
            write_res = await file_tool.invoke("write_file", filepath=cache_path, content=serialized)
        except Exception as exc:
            logger.warning("工具结果落盘失败，保留原始结果: %s", exc)
            return result
        if not write_res.success:
            return result
        digest = serialized[:500]
        return ToolResult(
            success=result.success,
            message=(
                f"完整结果已保存到 {cache_path}（{len(serialized)} 字符）。"
                "摘要预览如下，如需完整内容请用 read_file 读取该路径。"
            ),
            data=digest,
        )

    async def _ensure_memory(self) -> None:
        """确保智能体记忆是存在的"""
        if self._memory is None:
            async with self._uow_factory() as uow:
                self._memory = await uow.session.get_memory(self._session_id, self.name)

    def _build_system_content(self) -> str:
        system_content = self._system_prompt
        if self._skill_prompt:
            system_content += f"\n\n--- Skill Instructions ---\n{self._skill_prompt}"
        if self._long_term_memory_block:
            system_content += f"\n\n{self._long_term_memory_block}"
        return system_content

    def _ensure_system_message(self) -> bool:
        system_content = self._build_system_content()
        messages = self._memory.get_messages()
        if not messages:
            self._memory.add_message({"role": "system", "content": system_content})
            return True
        if messages[0].get("role") == "system" and messages[0].get("content") != system_content:
            messages[0]["content"] = system_content
            self._memory.messages = messages
            return True
        if messages[0].get("role") != "system":
            self._memory.messages = [{"role": "system", "content": system_content}] + messages
            return True
        return False

    def set_current_step(self, step: str) -> None:
        self._current_step = step or "default"

    def _estimate_memory_tokens(self) -> int:
        if self._last_prompt_tokens > 0:
            return self._last_prompt_tokens
        if self._memory:
            return estimate_messages_tokens(self._memory.get_messages())
        return 0

    def _estimate_current_memory_tokens(self) -> int:
        if self._memory:
            return estimate_messages_tokens(self._memory.get_messages())
        return 0

    @staticmethod
    def _align_keep_boundary(messages: List[Dict[str, Any]], split_idx: int) -> int:
        idx = max(1, split_idx)
        while idx < len(messages) and messages[idx].get("role") == "tool":
            idx += 1
        while idx > 1 and messages[idx - 1].get("role") == "tool":
            idx -= 1
        if idx > 1:
            prev = messages[idx - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                while idx > 1 and messages[idx - 1].get("role") == "tool":
                    idx -= 1
        return max(1, idx)

    async def _record_token_usage(self, usage: Dict[str, int]) -> None:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return
        self._last_prompt_tokens = prompt_tokens
        try:
            self._pending_usage_event = await self._token_accountant.record(usage, self._current_step)
        except Exception as exc:
            logger.warning("记录 token 用量失败: %s", exc)

    async def _invoke_llm(
            self,
            messages: List[Dict[str, Any]],
            format: Optional[str] = None,
            stream_id: Optional[str] = None,
            emit_deltas: bool = True,
            response_schema: Optional[Type[BaseModel]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """调用语言模型并处理记忆内容，实时向调用方输出流式 delta 事件。"""
        await self._add_to_memory(messages, persist=False)

        available_tools = self._get_available_tools()
        response_schema_payload = schema_payload(response_schema) if response_schema is not None else None
        effective_format = format
        if effective_format == "json_object" and available_tools:
            logger.debug("工具可用时跳过 json_object response_format，以兼容 tool_calls")
            effective_format = None
        response_format = {"type": effective_format} if effective_format else None
        effective_tools = [] if response_schema_payload is not None else available_tools
        effective_tool_choice = None if response_schema_payload is not None else self._tool_choice
        capabilities = vision_service.resolve_capabilities(self._llm)
        strip_images = bool(
            effective_tools
            and capabilities.vision
            and not capabilities.vision_with_tools
        )

        error = "调用语言模型发生错误"
        stripped_images_for_retry = False
        for _ in range(self._agent_config.max_retries):
            self._last_llm_message = None
            try:
                stream_id = stream_id or str(uuid.uuid4())
                aggregated: Dict[str, Any] = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "",
                    "tool_calls": [],
                }
                tool_call_acc: Dict[int, Dict[str, Any]] = {}

                call_usage: Dict[str, int] = {}
                async for delta in self._llm.stream_invoke(
                        messages=self._messages_for_llm(
                            self._memory.get_messages(),
                            self._llm,
                            strip_images=strip_images,
                        ),
                        tools=effective_tools,
                        response_format=response_format,
                        tool_choice=effective_tool_choice,
                        response_schema=response_schema_payload,
                        retry_budget=self._retry_budget,
                ):
                    if usage_delta := delta.get("usage"):
                        call_usage = usage_delta
                        continue
                    if content := delta.get("content"):
                        aggregated["content"] += content
                        if emit_deltas:
                            yield MessageDeltaEvent(stream_id=stream_id, delta=content)
                    if reasoning := delta.get("reasoning_content"):
                        aggregated["reasoning_content"] += reasoning
                        if emit_deltas:
                            yield ReasoningDeltaEvent(stream_id=stream_id, delta=reasoning)
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
                            if emit_deltas:
                                yield ToolArgsDeltaEvent(
                                    stream_id=stream_id,
                                    tool_call_id=acc["id"] or f"pending-{idx}",
                                    tool_name=acc["function"]["name"],
                                    delta=fn["arguments"],
                                )

                if tool_call_acc:
                    aggregated["tool_calls"] = [
                        tool_call_acc[i] for i in sorted(tool_call_acc.keys())
                    ]

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

                await self._add_to_memory([filtered_message], persist=False)
                if call_usage:
                    await self._record_token_usage(call_usage)
                self._last_llm_message = filtered_message
                return
            except Exception as e:
                if isinstance(e, RetryBudgetExceeded):
                    raise RuntimeError(str(e)) from e
                if isinstance(e, ModelUnavailableError):
                    if not stripped_images_for_retry and await self._strip_images_from_memory():
                        stripped_images_for_retry = True
                        logger.warning("多模态请求失败，已从记忆中剥离图片并重试")
                        await asyncio.sleep(self._retry_interval)
                        continue
                    raise
                if is_retriable_llm_error(e):
                    raise
                error_msg = _format_agent_error(e)
                logger.error(
                    f"调用语言模型发生错误: {error_msg}",
                    exc_info=not hasattr(e, "msg"),
                )
                error = error_msg
                await asyncio.sleep(self._retry_interval)
                continue

        raise RuntimeError(f"调用语言模型失败, 已达到最大重试次数({self._agent_config.max_retries}): {error}")

    async def _invoke_tool(self, tool: BaseTool, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """传递工具包+工具名字+对应参数调用指定工具"""
        started = time.monotonic()
        normalized_args = arguments if isinstance(arguments, dict) else {}
        # 1.执行循环调用工具获取结果
        err = ""
        timeout_seconds = max(1, self._runtime_settings.tool_timeout_seconds)
        for _ in range(self._agent_config.max_retries):
            try:
                result = await asyncio.wait_for(
                    tool.invoke(tool_name, **arguments),
                    timeout=timeout_seconds,
                )
                if tool.name in {"mcp", "a2a"} and not result.success:
                    err = result.message or f"调用工具[{tool_name}]失败"
                    logger.warning("调用工具[%s]返回失败，准备重试: %s", tool_name, err)
                    await asyncio.sleep(self._retry_interval)
                    continue
                truncated = self._truncate_tool_result(
                    result,
                    self._agent_config.tool_result_max_chars,
                    function_name=tool_name,
                    function_args=normalized_args,
                )
                await self._maybe_record_tool_audit(
                    tool_name=tool_name,
                    arguments=normalized_args,
                    result=truncated,
                    started=started,
                )
                return truncated
            except asyncio.TimeoutError:
                err = f"调用工具[{tool_name}]超时({timeout_seconds}s)"
                logger.warning(err)
                await asyncio.sleep(self._retry_interval)
                continue
            except Exception as e:
                err = str(e)
                logger.exception(f"调用工具[{tool_name}]出错, 错误: {str(e)}")
                await asyncio.sleep(self._retry_interval)
                continue

        # 2.循环最大重试次数后没有结果则将错误作为工具的执行结果，让LLM自行处理
        failed = ToolResult(success=False, message=err)
        await self._maybe_record_tool_audit(
            tool_name=tool_name,
            arguments=normalized_args,
            result=failed,
            started=started,
        )
        return failed

    async def _strip_images_from_memory(self) -> bool:
        """Remove image parts from persisted memory after multimodal rejection."""
        await self._ensure_memory()
        messages = self._memory.get_messages()
        if not vision_service.messages_contain_images(messages):
            return False
        self._memory.messages = vision_service.strip_image_parts_from_messages(messages)
        await self._persist_memory()
        return True

    async def _add_to_memory(self, messages: List[Dict[str, Any]], persist: bool = True) -> None:
        """将对应的信息添加到记忆中"""
        # 1.先检查确保记忆是存在的
        await self._ensure_memory()

        # 2.确保系统提示与当前 Skill / 长期记忆配置保持同步
        system_changed = self._ensure_system_message()

        # 3.将正常消息添加到记忆中
        self._memory.add_messages(messages)

        # 4.将记忆持久化到数据仓库中
        if persist or system_changed:
            async with self._uow_factory() as uow:
                await uow.session.save_memory(self._session_id, self.name, self._memory)

    async def _persist_memory(self) -> None:
        await self._ensure_memory()
        async with self._uow_factory() as uow:
            await uow.session.save_memory(self._session_id, self.name, self._memory)

    async def compact_memory(self) -> None:
        """压缩Agent的记忆（仅规则裁剪）。"""
        await self._ensure_memory()
        memory_cfg = self._runtime_settings.memory
        self._memory.compact(tool_content_max_chars=memory_cfg.compact_tool_content_max_chars)
        async with self._uow_factory() as uow:
            await uow.session.save_memory(self._session_id, self.name, self._memory)

    async def summarize_and_compact(self) -> None:
        """混合记忆压缩：规则裁剪 + 超阈值时 LLM 摘要。"""
        memory_cfg = self._runtime_settings.memory
        strategy = (memory_cfg.compact_strategy or "hybrid").lower()
        if not memory_cfg.compact_always_on_step_boundary:
            await self._ensure_memory()
            if self._estimate_current_memory_tokens() < memory_cfg.compact_rule_trigger_threshold:
                return
        await self.compact_memory()
        if strategy == "rule":
            return
        if self._estimate_current_memory_tokens() < memory_cfg.compact_token_threshold:
            return
        if strategy not in {"llm", "hybrid"}:
            return
        await self._ensure_memory()
        messages = self._memory.get_messages()
        keep_recent = max(4, memory_cfg.compact_keep_recent)
        if len(messages) <= keep_recent + 2:
            return
        split_idx = len(messages) - keep_recent
        split_idx = self._align_keep_boundary(messages, split_idx)
        prefix = messages[:1] if messages and messages[0].get("role") == "system" else []
        start_idx = len(prefix)
        if split_idx <= start_idx:
            return
        middle = messages[start_idx:split_idx]
        suffix = messages[split_idx:]
        if not middle:
            return
        history_text = "\n".join(
            f"{m.get('role')}: {str(m.get('content', ''))[:800]}"
            for m in middle
        )[:12000]
        try:
            summary_response = await self._llm.invoke([{
                "role": "user",
                "content": _MEMORY_SUMMARY_PROMPT.format(history=history_text),
            }])
            summary_text = summary_response.get("content") or ""
            if not summary_text and summary_response.get("reasoning_content"):
                summary_text = summary_response.get("reasoning_content") or ""
            usage = summary_response.get("_usage")
            if usage:
                await self._record_token_usage(usage)
            if not summary_text.strip():
                logger.warning("记忆 LLM 摘要为空，跳过替换")
                return
            self._memory.messages = prefix + [{
                "role": "user",
                "content": f"[历史摘要 — 此前对话已压缩]\n{summary_text.strip()}",
            }] + suffix
            async with self._uow_factory() as uow:
                await uow.session.save_memory(self._session_id, self.name, self._memory)
            logger.info(
                "Agent[%s] LLM 记忆摘要完成: middle=%s kept=%s tokens_est=%s",
                self.name,
                len(middle),
                len(suffix),
                self._estimate_memory_tokens(),
            )
        except Exception as exc:
            logger.warning("Agent[%s] LLM 记忆摘要失败，保留规则压缩结果: %s", self.name, exc)

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
        async with self._uow_factory() as uow:
            await uow.session.save_memory(self._session_id, self.name, self._memory)

    async def invoke(
            self,
            query: str,
            format: Optional[str] = None,
            vision_attachments: Optional[List[VisionAttachment]] = None,
            emit_deltas: bool = True,
            response_schema: Optional[Type[BaseModel]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """传递消息+响应格式调用程序生成异步迭代内容"""
        try:
            async for event in self._invoke_inner(
                    query,
                    format,
                    vision_attachments,
                    emit_deltas,
                    response_schema,
            ):
                yield event
        finally:
            await self._token_accountant.flush()

    async def _invoke_inner(
            self,
            query: str,
            format: Optional[str],
            vision_attachments: Optional[List[VisionAttachment]],
            emit_deltas: bool,
            response_schema: Optional[Type[BaseModel]],
    ) -> AsyncGenerator[BaseEvent, None]:
        try:
            async for event in self._invoke_inner_body(
                    query,
                    format,
                    vision_attachments,
                    emit_deltas,
                    response_schema,
            ):
                yield event
        finally:
            await self._persist_memory()

    async def _invoke_inner_body(
            self,
            query: str,
            format: Optional[str],
            vision_attachments: Optional[List[VisionAttachment]],
            emit_deltas: bool,
            response_schema: Optional[Type[BaseModel]],
    ) -> AsyncGenerator[BaseEvent, None]:
        # 1.需要判断下是否传递了format
        format = format if format else self._format

        # 2.调用语言模型获取响应内容
        attachments_to_use = vision_attachments
        if vision_attachments and self._memory:
            refs = [a.ref_url for a in vision_attachments if a.ref_url]
            if refs and vision_service.memory_contains_image_refs(self._memory.get_messages(), refs):
                attachments_to_use = None

        user_message = vision_service.build_user_message(
            query,
            attachments_to_use,
            llm=self._llm,
        )
        async for event in self._invoke_llm(
                [user_message],
                format,
                emit_deltas=emit_deltas,
                response_schema=response_schema,
        ):
            yield event
        message = self._last_llm_message
        if self._pending_usage_event:
            yield self._pending_usage_event
            self._pending_usage_event = None

        async for event in self._run_tool_iteration_loop(
                message,
                format,
                emit_deltas=emit_deltas,
                response_schema=response_schema,
        ):
            yield event

    async def continue_tool_iteration_loop(
            self,
            *,
            inject_tool_messages: Optional[List[Dict[str, Any]]] = None,
            format: Optional[str] = None,
            emit_deltas: bool = True,
            response_schema: Optional[Type[BaseModel]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Resume ReAct loop after tool gate / takeover injects tool results."""
        await self._ensure_memory()
        fmt = format if format is not None else self._format

        if inject_tool_messages:
            self._memory.add_messages(inject_tool_messages)
            llm_input = inject_tool_messages
        else:
            last = self._memory.get_last_message()
            if last and last.get("role") == "tool":
                llm_input = [last]
            else:
                message = self._last_llm_message or last
                async for event in self._run_tool_iteration_loop(
                        message,
                        fmt,
                        emit_deltas=emit_deltas,
                        response_schema=response_schema,
                ):
                    yield event
                return

        async for event in self._invoke_llm(
                llm_input,
                format=None,
                emit_deltas=emit_deltas,
                response_schema=response_schema,
        ):
            yield event
        message = self._last_llm_message
        if self._pending_usage_event:
            yield self._pending_usage_event
            self._pending_usage_event = None
        async for event in self._run_tool_iteration_loop(
                message,
                fmt,
                emit_deltas=emit_deltas,
                response_schema=response_schema,
        ):
            yield event

    async def _run_tool_iteration_loop(
            self,
            message: Optional[Dict[str, Any]],
            format: Optional[str],
            *,
            emit_deltas: bool,
            response_schema: Optional[Type[BaseModel]],
    ) -> AsyncGenerator[BaseEvent, None]:
        format = format if format else self._format

        # 3.循环遍历直到最大迭代次数
        for _ in range(self._agent_config.max_iterations):
            # 4.如果LLM响应为空或无工具调用则表示LLM生成了文本回答，这时候就是最终答案
            if not message or not message.get("tool_calls"):
                break

            tool_calls = message.get("tool_calls") or []
            tool_messages: List[Dict[str, Any]] = []

            async def _run_tool_call(tool_call: Dict[str, Any]) -> tuple[List[BaseEvent], List[Dict[str, Any]], bool]:
                events: List[BaseEvent] = []
                msgs: List[Dict[str, Any]] = []
                if not tool_call.get("function"):
                    return events, msgs, False

                tool_call_id = tool_call["id"] or str(uuid.uuid4())
                function_name = normalize_tool_name(tool_call["function"]["name"])
                raw_arguments = tool_call["function"]["arguments"]
                if isinstance(raw_arguments, dict):
                    function_args = raw_arguments
                else:
                    function_args = await self._json_parser.invoke(raw_arguments)

                if self._allowed_tool_names is not None and not is_tool_allowed(
                        function_name, self._allowed_tool_names
                ):
                    result = ToolResult(success=False, message=f"工具[{function_name}]未被当前Skill授权")
                    events.append(ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name="blocked",
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
                    return events, msgs, False

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
                    return events, msgs, False

                async def _execute() -> tuple[List[BaseEvent], List[Dict[str, Any]], bool]:
                    inner_events: List[BaseEvent] = []
                    inner_msgs: List[Dict[str, Any]] = []
                    inner_events.append(ToolEvent(
                        tool_call_id=tool_call_id,
                        tool_name=tool.name,
                        function_name=function_name,
                        function_args=function_args,
                        status=ToolEventStatus.CALLING,
                    ))
                    if await self._require_first_visit_domain_gate(
                            function_name,
                            function_args if isinstance(function_args, dict) else {},
                            tool_call_id,
                            inner_events,
                    ):
                        return inner_events, [], True
                    if await self._require_tool_approval_gate(
                            function_name,
                            function_args if isinstance(function_args, dict) else {},
                            tool_call_id,
                            inner_events,
                    ):
                        return inner_events, [], True
                    result = await self._invoke_tool(tool, function_name, function_args)
                    if function_name == "browser_navigate" and result.success:
                        await self._record_visited_domain(function_args)
                    result = await self._offload_large_result(tool_call_id, function_name, result)
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
                        tool_content, extra_messages = await self._build_browser_tool_payload(
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
                    return inner_events, inner_msgs, False

                if tool.name in STATEFUL_TOOL_NAMES:
                    async with self._stateful_tool_lock:
                        return await _execute()
                return await _execute()

            results = await asyncio.gather(*[_run_tool_call(tc) for tc in tool_calls])
            if any(wait_flag for _, _, wait_flag in results):
                for event_list, _, _ in results:
                    for event in event_list:
                        yield event
                yield WaitEvent()
                return
            for events, msgs, _ in results:
                for event in events:
                    yield event
                tool_messages.extend(msgs)

            # Drain auxiliary events from tools (e.g. SubAgentEvent)
            for tool in self._tools:
                drain = getattr(tool, "drain_events", None)
                if callable(drain):
                    for aux_event in drain():
                        yield aux_event

            async for event in self._invoke_llm(tool_messages, format=None, emit_deltas=emit_deltas):
                yield event
            message = self._last_llm_message
            if self._pending_usage_event:
                yield self._pending_usage_event
                self._pending_usage_event = None
        else:
            # 13.超过最大迭代次数后，则抛出错误
            yield ErrorEvent(
                error=f"Agent迭代超过最大迭代次数: {self._agent_config.max_iterations}, 任务处理失败",
                code=TASK_INFRA_FAILED,
            )

        # 14.在指定步骤内完成了迭代则返回消息事件
        if message and message.get("content") is not None:
            yield MessageEvent(
                message=message["content"],
                stream_id=message.get("stream_id"),
            )
        else:
            yield ErrorEvent(error="Agent未能生成有效回复内容", code=MODEL_UNAVAILABLE)

    @staticmethod
    def _normalize_domain(url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return (parsed.hostname or "").lower()

    def _effective_gate_profile(self) -> str:
        return (self._runtime_settings.gate_profile or "standard").lower()

    def _gate_profile_settings(self):
        runtime = get_runtime_config()
        return resolve_gate_profile_settings(self._effective_gate_profile(), runtime.hitl)

    def _tool_gate_call_level_enabled(self) -> bool:
        runtime = get_runtime_config()
        if not runtime.feature_flags.enable_hitl_gates:
            return False
        override = self._runtime_settings.tool_gate_call_level_enabled
        if override is not None:
            return bool(override)
        if self._runtime_settings.gate_profile:
            return bool(self._gate_profile_settings().tool_gate_call_level_enabled)
        return runtime.hitl.tool_gate_call_level_enabled

    def _should_audit_tool(self, tool_name: str) -> bool:
        if not self._runtime_settings.gate_profile:
            return False
        lowered = tool_name.lower()
        if lowered.startswith("browser_"):
            return True
        if lowered in {"shell_execute", "a2a"}:
            return True
        if lowered.startswith("mcp_") or tool_name == "mcp":
            return True
        return False

    def _compute_tool_gated_flag(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """Whether this tool call would match per-call gate rules (risk list / critical action)."""
        if not self._runtime_settings.gate_profile:
            return False
        runtime = get_runtime_config()
        if not tool_matches_risk_list(tool_name, runtime.hitl.tool_gate_risk_list):
            return False
        profile_settings = self._gate_profile_settings()
        if profile_settings.selective_critical_only:
            return matches_critical_action(
                tool_name,
                arguments,
                runtime.hitl.critical_action_patterns,
            )
        return self._tool_gate_call_level_enabled()

    async def _maybe_record_tool_audit(
            self,
            *,
            tool_name: str,
            arguments: Dict[str, Any],
            result: ToolResult,
            started: float,
    ) -> None:
        if not self._should_audit_tool(tool_name):
            return
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            await self._record_tool_audit(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                duration_ms=duration_ms,
                gated=self._compute_tool_gated_flag(tool_name, arguments),
            )
        except Exception:
            logger.exception("写入工具审计失败 session=%s tool=%s", self._session_id, tool_name)

    async def _record_tool_audit(
            self,
            *,
            tool_name: str,
            arguments: Dict[str, Any],
            result: ToolResult,
            duration_ms: int,
            gated: bool = False,
    ) -> None:
        if not self._should_audit_tool(tool_name):
            return
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            await uow.audit.add(AuditLog(
                actor_user_id=session.owner_user_id if session else None,
                action="agent_tool_invoke",
                resource_type="session",
                resource_id=self._session_id,
                team_id=session.team_id if session else None,
                metadata={
                    "tool": tool_name,
                    "args": redact_tool_args(arguments if isinstance(arguments, dict) else {}),
                    "success": result.success,
                    "result_summary": summarize_tool_result(result),
                    "duration_ms": duration_ms,
                    "gate_profile": self._runtime_settings.gate_profile,
                    "gated": gated,
                },
            ))
            await uow.commit()

    async def _record_visited_domain(self, function_args: Dict[str, Any]) -> None:
        url = function_args.get("url") if isinstance(function_args, dict) else None
        domain = self._normalize_domain(str(url or ""))
        if not domain:
            return
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session:
                return
            meta = session.pending_metadata or {}
            visited = list(meta.get("visited_domains") or [])
            if domain in visited:
                return
            visited.append(domain)
            await uow.session.set_pending_metadata(
                self._session_id,
                merge_pending_metadata(meta, {"visited_domains": visited}),
            )

    async def _require_first_visit_domain_gate(
            self,
            function_name: str,
            function_args: Dict[str, Any],
            tool_call_id: str,
            events: List[BaseEvent],
    ) -> bool:
        runtime = get_runtime_config()
        if not runtime.feature_flags.enable_hitl_gates:
            return False
        if function_name != "browser_navigate":
            return False
        url = function_args.get("url")
        if not url:
            return False
        domain = self._normalize_domain(str(url))
        if not domain:
            return False
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session:
                return False
            if not session.operator_scope:
                return False
            whitelist = list(self._runtime_settings.operator_domains or session.operator_domains or [])
            if domain_in_whitelist(domain, whitelist):
                return False
            meta = session.pending_metadata or {}
            visited = set(meta.get("visited_domains") or [])
            approved = set(meta.get("approved_domains") or [])
            if domain in visited or domain in approved:
                return False
        if self._sandbox_lifecycle:
            await self._sandbox_lifecycle.create_tool_checkpoint(function_name, tool_call_id)
        return await self._enter_tool_approval_gate(
            function_name=function_name,
            function_args=function_args,
            tool_call_id=tool_call_id,
            events=events,
            extra_metadata={"first_visit_domain": domain},
            approval_note=f"首次访问域名: {domain}",
        )

    async def _require_tool_approval_gate(
            self,
            function_name: str,
            function_args: Dict[str, Any],
            tool_call_id: str,
            events: List[BaseEvent],
    ) -> bool:
        if not self._tool_gate_call_level_enabled():
            return False
        runtime = get_runtime_config()
        if not tool_matches_risk_list(function_name, runtime.hitl.tool_gate_risk_list):
            return False
        profile_settings = (
            self._gate_profile_settings()
            if self._runtime_settings.gate_profile
            else None
        )
        if profile_settings and profile_settings.selective_critical_only:
            if not matches_critical_action(
                    function_name,
                    function_args if isinstance(function_args, dict) else {},
                    runtime.hitl.critical_action_patterns,
            ):
                return False
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session:
                return False
            meta = session.pending_metadata or {}
            approved = meta.get("approved_tools") or []
            if any(tool_matches_risk_list(function_name, [item]) for item in approved):
                return False
            if not session.operator_scope:
                return False
        if self._sandbox_lifecycle:
            await self._sandbox_lifecycle.create_tool_checkpoint(function_name, tool_call_id)
        return await self._enter_tool_approval_gate(
            function_name=function_name,
            function_args=function_args,
            tool_call_id=tool_call_id,
            events=events,
        )

    async def _enter_tool_approval_gate(
            self,
            function_name: str,
            function_args: Dict[str, Any],
            tool_call_id: str,
            events: List[BaseEvent],
            extra_metadata: Optional[Dict[str, Any]] = None,
            approval_note: Optional[str] = None,
    ) -> bool:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session:
                return False
            meta = session.pending_metadata or {}
            pending_tool_call = {
                "tool_call_id": tool_call_id,
                "tool_name": function_name,
                "args": function_args,
            }
            if extra_metadata:
                pending_tool_call.update(extra_metadata)
            await uow.session.set_pending_metadata(
                self._session_id,
                merge_pending_metadata(meta, {
                    "pending_tool_call": pending_tool_call,
                }),
            )
            await uow.session.set_pending_phase(self._session_id, TOOL_APPROVAL_PHASE)
        payload = {
            "tool_call_id": tool_call_id,
            "tool_name": function_name,
            "args": function_args,
        }
        if extra_metadata:
            payload.update(extra_metadata)
        if approval_note:
            payload["note"] = approval_note
        events.append(ApprovalEvent(
            approval_id=str(uuid.uuid4()),
            kind="tool",
            payload=payload,
            options=["approve", "approve_same", "reject"],
        ))
        return True
