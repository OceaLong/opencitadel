#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Memory compaction helpers extracted from BaseAgent."""
import logging
from typing import Callable, List, Optional

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.repositories.uow import IUnitOfWork
from core.config import get_settings

logger = logging.getLogger(__name__)

_MEMORY_SUMMARY_PROMPT = """请将以下 Agent 对话历史压缩为简洁摘要，保留：
- 已完成的关键操作与结论
- 重要文件路径、数据、错误信息
- 用户目标与当前进度

只输出摘要正文，不要 JSON。使用与历史相同的语言。

历史消息:
{history}
"""


class AgentMemoryCompactor:
    """Rule-based and LLM-based session memory compaction."""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            json_parser: JSONParser,
            session_id: str,
            agent_name: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._json_parser = json_parser
        self._session_id = session_id
        self._agent_name = agent_name

    async def load_memory(self) -> Memory:
        async with self._uow_factory() as uow:
            return await uow.session.get_memory(self._session_id, self._agent_name)

    async def save_memory(self, memory: Memory) -> None:
        async with self._uow_factory() as uow:
            await uow.session.save_memory(self._session_id, self._agent_name, memory)

    async def summarize_and_compact(self, last_prompt_tokens: int) -> None:
        settings = get_settings()
        memory = await self.load_memory()
        memory.compact()
        threshold = settings.memory_compact_token_threshold
        if (
                settings.memory_compact_strategy in ("hybrid", "llm")
                and last_prompt_tokens >= threshold
                and len(memory.get_messages()) > 4
        ):
            try:
                history = "\n".join(
                    str(m.get("content", ""))[:500] for m in memory.get_messages()[-20:]
                )
                response = await self._llm.invoke([{
                    "role": "user",
                    "content": _MEMORY_SUMMARY_PROMPT.format(history=history),
                }])
                summary = response.get("content") or ""
                if summary.strip():
                    memory.messages = [
                        {"role": "system", "content": f"[历史摘要]\n{summary.strip()}"},
                        *memory.get_messages()[-6:],
                    ]
            except Exception as e:
                logger.warning("LLM memory summary failed: %s", e)
        await self.save_memory(memory)
