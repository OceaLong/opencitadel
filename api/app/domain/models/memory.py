#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_IMAGE_OMITTED_TEXT = "[image omitted in compact]"
_TRUNCATED_TOOL_PREFIX = "[truncated] "
_DEFAULT_TOOL_CONTENT_MAX = 2000

_TRUNCATABLE_TOOL_NAMES = frozenset({
    "search_web",
    "read_file",
    "write_file",
    "replace_in_file",
    "search_in_file",
    "find_files",
    "shell",
    "exec_command",
    "analyze_image",
})


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
                    return tool_call.get("function", {}).get("name")
            if tool_calls:
                return tool_calls[0].get("function", {}).get("name")
        return None

    def _truncate_tool_content(self, message: Dict[str, Any], function_name: Optional[str], max_chars: int) -> None:
        if function_name not in _TRUNCATABLE_TOOL_NAMES:
            return
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= max_chars:
            return
        message["content"] = (
            _TRUNCATED_TOOL_PREFIX + content[: max_chars - len(_TRUNCATED_TOOL_PREFIX)]
            + f"... ({len(content)} chars total)"
        )

    def compact(self, tool_content_max_chars: int = _DEFAULT_TOOL_CONTENT_MAX) -> None:
        """记忆压缩，将记忆中已经执行的工具(搜索/网页源码获取/浏览器访问结果等)这类已经执行过的消息进行压缩检索"""
        last_image_index: Optional[int] = None
        for index, message in enumerate(self.messages):
            if _message_has_image_part(message):
                last_image_index = index

        # 1.循环遍历所有的消息列表
        for index, message in enumerate(self.messages):
            # 2.判断消息的角色是否为tool
            if self.get_message_role(message) == "tool":
                function_name = self._resolve_tool_function_name(index, message)
                if function_name in ["browser_view", "browser_navigate", "browser_screenshot"]:
                    message["content"] = "(removed)"
                    logger.debug(f"从记忆中移除对应工具的结果: {function_name}")
                else:
                    self._truncate_tool_content(message, function_name, tool_content_max_chars)

            if _message_has_image_part(message) and index != last_image_index:
                _strip_image_parts_from_message(message)
                logger.debug("从记忆中压缩历史多模态图片 part: index=%s", index)

            # 3.仅移除非 assistant 消息中的 reasoning_content；thinking 模式要求
            #    assistant 历史必须回传 reasoning_content，否则后续 LLM 调用会 400
            if (
                "reasoning_content" in message
                and message.get("role") != "assistant"
            ):
                logger.debug(
                    f"从记忆中移除非 assistant 思考结果: "
                    f"{str(message['reasoning_content'])[:50]}..."
                )
                del message["reasoning_content"]

    @property
    def empty(self) -> bool:
        """只读属性，检查记忆是否为空"""
        return len(self.messages) == 0
