#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dedicated sub-agent for delegated subtasks (free-form summary output)."""
from typing import Optional

from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.system import SYSTEM_PROMPT

SUBAGENT_SYSTEM_PROMPT = """
你是一个专注的子任务执行 Agent。你会收到一个自包含的子目标，请独立完成它并返回简洁的结果摘要。

要求：
- 只关注当前子目标，不要扩展范围。
- 使用可用工具直接执行，不要向用户提问（除非子目标本身需要外部信息且无法推断）。
- 完成后用自然语言总结关键结论、文件路径与错误（如有）。
- 不要返回 JSON，直接输出可读的摘要正文。
"""


class SubAgentAgent(BaseAgent):
    """Lightweight agent for isolated subtask execution."""

    name: str = "subagent"
    _system_prompt: str = SYSTEM_PROMPT + SUBAGENT_SYSTEM_PROMPT
    _format: Optional[str] = None
