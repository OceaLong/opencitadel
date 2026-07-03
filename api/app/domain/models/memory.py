#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_IMAGE_OMITTED_TEXT = "[image omitted in compact]"
_TRUNCATED_TOOL_PREFIX = "[truncated] "
_DEFAULT_TOOL_CONTENT_MAX = 8000

_BROWSER_COMPACT_FUNCTIONS = frozenset({
    "browser_view",
    "browser_navigate",
    "browser_screenshot",
    "browser_restart",
})

_TRUNCATABLE_TOOL_NAMES = frozenset({
    "search_web",
    "read_file",
    "replace_in_file",
    "search_in_file",
    "find_files",
    "shell",
    "shell_execute",
    "exec_command",
    "analyze_image",
})

# Keys to include in truncation hints per tool name.
_TRUNCATION_ARG_KEYS: Dict[str, tuple[str, ...]] = {
    "read_file": ("path", "filepath"),
    "replace_in_file": ("path", "filepath"),
    "search_in_file": ("path", "filepath"),
    "find_files": ("path", "pattern"),
    "search_web": ("query",),
    "shell_execute": ("command",),
    "shell": ("command",),
}


def _extract_url_from_tool_content(content: Any) -> Optional[str]:
    """Parse tool result JSON (or multimodal parts) and return page URL if present."""
    if content is None:
        return None
    try:
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                text = part.get("text") or ""
                url = _extract_url_from_tool_content(text)
                if url:
                    return url
            return None
        if isinstance(content, dict):
            payload = content
        else:
            text = str(content).strip()
            if not text:
                return None
            payload = json.loads(text)
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
        url = payload.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _browser_compact_placeholder(url: Optional[str]) -> str:
    if url:
        return (
            f"(页面内容已从上下文移除以节省空间；最近访问 URL: {url}，"
            "如需重新获取请调用 browser_navigate 或 browser_view)"
        )
    return "(removed)"


def _parse_tool_call_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _format_truncation_call_hint(function_name: Optional[str], arguments: Dict[str, Any]) -> str:
    if not function_name or not arguments:
        return ""
    keys = _TRUNCATION_ARG_KEYS.get(function_name, ())
    parts: List[str] = []
    for key in keys:
        value = arguments.get(key)
        if value is None or value == "":
            continue
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f'{key}="{text}"')
    if not parts:
        return ""
    return f"原始调用: {function_name}({', '.join(parts)})。 "


def _message_has_image_part(message: Dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}
        for part in content
    )


def _strip_image_parts_from_message(message: Dict[str, Any]) -> None:
    content = message.get("content")
    if not isinstance(content, list):
        return
    new_parts: List[Dict[str, Any]] = []
    had_image = False
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"image_url", "image_ref"}:
            had_image = True
            continue
        new_parts.append(part)
    if had_image:
        new_parts.append({"type": "text", "text": _IMAGE_OMITTED_TEXT})
    if new_parts:
        message["content"] = new_parts
    elif had_image:
        message["content"] = _IMAGE_OMITTED_TEXT


class Memory(BaseModel):
    """记忆类，定义Agent的记忆基础信息"""
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def get_message_role(cls, message: Dict[str, Any]) -> str:
        """根据传递的消息来获取消息的角色信息"""
        return message.get("role")

    def add_message(self, message: Dict[str, Any]) -> None:
        """往记忆中添加一条消息"""
        self.messages.append(message)

    def add_messages(self, messages: List[Dict[str, Any]]) -> None:
        """往记忆中添加多条消息"""
        self.messages.extend(messages)

    def get_messages(self) -> List[Dict[str, Any]]:
        """获取记忆中的所有消息列表"""
        return self.messages

    def get_last_message(self) -> Optional[Dict[str, Any]]:
        """获取记忆中的最后一条消息，如果不存在则返回None"""
        return self.messages[-1] if len(self.messages) > 0 else None

    def roll_back(self) -> None:
        """回滚记忆，删除最后一条消息"""
        self.messages = self.messages[:-1]

    def _resolve_tool_function_name(self, index: int, message: Dict[str, Any]) -> Optional[str]:
        """解析 tool 消息对应的 function 名，优先内部字段，其次回溯 assistant tool_calls。"""
        function_name = message.get("_function_name") or message.get("function_name")
        if function_name:
            return function_name

        for previous in reversed(self.messages[:index]):
            if previous.get("role") != "assistant":
                continue
            tool_calls = previous.get("tool_calls") or []
            tool_call_id = message.get("tool_call_id")
            for tool_call in tool_calls:
                if tool_call.get("id") == tool_call_id:
                    fn = tool_call.get("function") or {}
                    return fn.get("name")
            if tool_calls:
                return tool_calls[0].get("function", {}).get("name")
        return None

    def _resolve_tool_call_arguments(self, index: int, message: Dict[str, Any]) -> Dict[str, Any]:
        for previous in reversed(self.messages[:index]):
            if previous.get("role") != "assistant":
                continue
            tool_call_id = message.get("tool_call_id")
            for tool_call in previous.get("tool_calls") or []:
                if tool_call.get("id") == tool_call_id:
                    fn = tool_call.get("function") or {}
                    return _parse_tool_call_arguments(fn.get("arguments"))
            break
        return {}

    def _truncate_tool_content(
            self,
            message: Dict[str, Any],
            function_name: Optional[str],
            max_chars: int,
            call_arguments: Optional[Dict[str, Any]] = None,
    ) -> None:
        if function_name not in _TRUNCATABLE_TOOL_NAMES:
            return
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= max_chars:
            return
        hint = _format_truncation_call_hint(function_name, call_arguments or {})
        notice = (
            f"\n\n{hint}[结果已截断: 原始长度 {len(content)} 字符，保留前 {max_chars} 字符。"
            "如需完整内容请缩小查询范围或使用 read_file 等工具分页获取。]"
        )
        budget = max(0, max_chars - len(_TRUNCATED_TOOL_PREFIX) - len(notice))
        message["content"] = (
            _TRUNCATED_TOOL_PREFIX + content[:budget] + notice
        )

    def compact(self, tool_content_max_chars: int = _DEFAULT_TOOL_CONTENT_MAX) -> None:
        """记忆压缩，将记忆中已经执行的工具(搜索/网页源码获取/浏览器访问结果等)这类已经执行过的消息进行压缩检索"""
        last_image_index: Optional[int] = None
        seen_image_refs: dict[str, int] = {}
        for index, message in enumerate(self.messages):
            if _message_has_image_part(message):
                last_image_index = index
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_ref":
                        ref = part.get("ref")
                        if ref:
                            seen_image_refs[ref] = index

        for index, message in enumerate(self.messages):
            if self.get_message_role(message) == "tool":
                function_name = self._resolve_tool_function_name(index, message)
                call_args = self._resolve_tool_call_arguments(index, message)
                if function_name in _BROWSER_COMPACT_FUNCTIONS:
                    url = _extract_url_from_tool_content(message.get("content"))
                    message["content"] = _browser_compact_placeholder(url)
                    logger.debug("Compacted browser tool result: %s url=%s", function_name, url)
                else:
                    self._truncate_tool_content(
                        message,
                        function_name,
                        tool_content_max_chars,
                        call_arguments=call_args,
                    )

            if _message_has_image_part(message):
                content = message.get("content")
                if isinstance(content, list):
                    refs_in_msg = [
                        p.get("ref") for p in content
                        if isinstance(p, dict) and p.get("type") == "image_ref" and p.get("ref")
                    ]
                    if index != last_image_index or any(
                        seen_image_refs.get(r, index) != index for r in refs_in_msg
                    ):
                        _strip_image_parts_from_message(message)
                        logger.debug("从记忆中压缩历史多模态图片 part: index=%s", index)

            if (
                "reasoning_content" in message
                and message.get("role") != "assistant"
            ):
                logger.debug(
                    "从记忆中移除非 assistant 思考结果: %s...",
                    str(message["reasoning_content"])[:50],
                )
                del message["reasoning_content"]

    @property
    def empty(self) -> bool:
        """只读属性，检查记忆是否为空"""
        return len(self.messages) == 0
