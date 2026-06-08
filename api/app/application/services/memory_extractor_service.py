#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
from typing import Callable, List

from app.domain.external.llm import LLM
from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """从以下会话内容中提炼3-10条值得长期记住的关键事实（用户偏好、重要结论、项目信息等）。
以JSON数组返回，每项格式: {{"title": "...", "content": "...", "tags": ["tag1"]}}
只返回JSON数组，不要其他文字。

会话事件摘要:
{events_summary}
"""

_MAX_EXTRACT_RETRIES = 2
_RETRY_INTERVAL = 1.0


def _extract_llm_text_content(response: dict) -> str:
    content = response.get("content") or ""
    if isinstance(content, str) and content.strip():
        return content
    reasoning = response.get("reasoning_content") or ""
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return "[]"


class MemoryExtractorService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            json_parser: RepairJSONParser,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._json_parser = json_parser

    async def extract_from_session(self, session_id: str) -> List[MemoryEntry]:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            return []

        events_summary = []
        for event in session.events[-30:]:
            events_summary.append(str(event.model_dump(mode="json") if hasattr(event, "model_dump") else event)[:500])

        parsed = None
        last_error = "未知错误"
        prompt = EXTRACT_PROMPT.format(events_summary="\n".join(events_summary)[:8000])
        for attempt in range(_MAX_EXTRACT_RETRIES):
            try:
                response = await self._llm.invoke([{"role": "user", "content": prompt}])
                content = _extract_llm_text_content(response)
                parsed = await self._json_parser.invoke(content, default_value=[])
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                if not isinstance(parsed, list):
                    return []
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"记忆自动抽取失败(第{attempt + 1}次): {e}")
                if attempt < _MAX_EXTRACT_RETRIES - 1:
                    await asyncio.sleep(_RETRY_INTERVAL)
        if parsed is None:
            logger.warning(f"记忆自动抽取失败，已降级跳过: {last_error}")
            return []

        entries = []
        async with self._uow_factory() as uow:
            for item in parsed[:10]:
                if not isinstance(item, dict):
                    continue
                entry = MemoryEntry(
                    scope=MemoryScope.SESSION,
                    session_id=session_id,
                    title=item.get("title", "")[:200],
                    content=item.get("content", "")[:2000],
                    tags=item.get("tags", []) if isinstance(item.get("tags"), list) else [],
                    source=MemorySource.AUTO_EXTRACTED,
                )
                if entry.title and entry.content:
                    await uow.memory_entry.save(entry)
                    entries.append(entry)
        from app.application.services.vector_memory_service import VectorMemoryService
        vector_service = VectorMemoryService()
        for entry in entries:
            await vector_service.store_embedding(entry.id, f"{entry.title}\n{entry.content}")
        logger.info(f"会话[{session_id}]自动抽取了{len(entries)}条记忆")
        return entries
