#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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

    def compact(self) -> None:
        """记忆压缩，将记忆中已经执行的工具(搜索/网页源码获取/浏览器访问结果等)这类已经执行过的消息进行压缩检索"""
        # 1.循环遍历所有的消息列表
        for index, message in enumerate(self.messages):
            # 2.判断消息的角色是否为tool
            if self.get_message_role(message) == "tool":
                function_name = self._resolve_tool_function_name(index, message)
                if function_name in ["browser_view", "browser_navigate"]:
                    message["content"] = "(removed)"
                    logger.debug(f"从记忆中移除对应工具的结果: {function_name}")

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
