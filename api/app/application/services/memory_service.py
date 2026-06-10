#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Callable, Dict, List, Optional

from app.application.errors.exceptions import NotFoundError, BadRequestError
from app.application.services.config_provider import get_runtime_config
from app.domain.utils.memory_recall import rank_entries_with_decay
from app.domain.models.memory import Memory
from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork], recall_limit: Optional[int] = None) -> None:
        self._uow_factory = uow_factory
        self._recall_limit_override = recall_limit

    @property
    def _recall_limit(self) -> int:
        if self._recall_limit_override is not None:
            return self._recall_limit_override
        return get_runtime_config().memory.recall_limit

    # --- Tier 1: session memories ---
    async def get_session_memories(self, session_id: str) -> Dict[str, List[dict]]:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            raise NotFoundError(f"会话[{session_id}]不存在")
        return {k: v.get_messages() for k, v in session.memories.items()}

    async def compact_session_memory(self, session_id: str, agent_name: str) -> None:
        async with self._uow_factory() as uow:
            memory = await uow.session.get_memory(session_id, agent_name)
            memory.compact()
            await uow.session.save_memory(session_id, agent_name, memory)

    async def clear_session_memory(self, session_id: str, agent_name: str) -> None:
        async with self._uow_factory() as uow:
            await uow.session.save_memory(session_id, agent_name, Memory(messages=[]))

    async def delete_session_memory_message(
            self, session_id: str, agent_name: str, index: int
    ) -> None:
        async with self._uow_factory() as uow:
            memory = await uow.session.get_memory(session_id, agent_name)
            messages = memory.get_messages()
            if index < 0 or index >= len(messages):
                raise NotFoundError(f"消息索引[{index}]不存在")
            memory.messages = messages[:index] + messages[index + 1:]
            await uow.session.save_memory(session_id, agent_name, memory)

    # --- Tier 2: long-term memory entries ---
    async def _validate_entry(self, entry: MemoryEntry) -> None:
        if entry.scope == MemoryScope.SESSION:
            if not entry.session_id:
                raise BadRequestError("scope=session 时必须提供 session_id")
            async with self._uow_factory() as uow:
                session = await uow.session.get_by_id(entry.session_id)
            if not session:
                raise NotFoundError(f"会话[{entry.session_id}]不存在")
        elif entry.session_id:
            entry.session_id = None

    async def list_entries(
            self,
            scope: Optional[MemoryScope] = None,
            session_id: Optional[str] = None,
            q: Optional[str] = None,
            tags: Optional[List[str]] = None,
    ) -> List[MemoryEntry]:
        async with self._uow_factory() as uow:
            return await uow.memory_entry.get_all(scope=scope, session_id=session_id, q=q, tags=tags)

    async def get_entry(self, entry_id: str) -> MemoryEntry:
        async with self._uow_factory() as uow:
            entry = await uow.memory_entry.get_by_id(entry_id)
        if not entry:
            raise NotFoundError(f"记忆[{entry_id}]不存在")
        return entry

    async def create_entry(self, entry: MemoryEntry) -> MemoryEntry:
        await self._validate_entry(entry)
        from app.application.services.vector_memory_service import get_vector_memory_service
        vector_service = get_vector_memory_service()
        async with self._uow_factory() as uow:
            await uow.memory_entry.save(entry)
            await vector_service.store_embedding(
                entry.id,
                f"{entry.title}\n{entry.content}",
                db_session=getattr(uow, "db_session", None),
            )
        return entry

    async def update_entry(self, entry_id: str, updates: MemoryEntry) -> MemoryEntry:
        await self._validate_entry(updates)
        async with self._uow_factory() as uow:
            existing = await uow.memory_entry.get_by_id(entry_id)
            if not existing:
                raise NotFoundError(f"记忆[{entry_id}]不存在")
            updates.id = entry_id
            await uow.memory_entry.save(updates)
            from app.application.services.vector_memory_service import get_vector_memory_service
            vector_service = get_vector_memory_service()
            await vector_service.store_embedding(
                entry_id,
                f"{updates.title}\n{updates.content}",
                db_session=getattr(uow, "db_session", None),
            )
        return updates

    async def delete_entry(self, entry_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.memory_entry.delete_by_id(entry_id)

    async def recall_for_session(self, session_id: str) -> str:
        """召回长期记忆并格式化为system块（时间衰减 + 可选向量混合检索）"""
        from app.application.services.config_provider import get_runtime_config
        from app.application.services.vector_memory_service import get_vector_memory_service

        query_text = ""
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
            if session:
                query_text = session.latest_message or ""
            entries = await uow.memory_entry.recall_for_session(
                session_id, limit=self._recall_limit
            )
            entries = rank_entries_with_decay(entries, self._recall_limit)

            if get_runtime_config().memory.vector_enabled and query_text.strip():
                vector_service = get_vector_memory_service()
                vector_entries = await vector_service.search_similar(
                    query_text,
                    session_id=session_id,
                    limit=self._recall_limit,
                    db_session=getattr(uow, "db_session", None),
                )
                by_id = {e.id: e for e in entries}
                seen = set(by_id.keys())
                for entry in vector_entries:
                    if entry.id in by_id:
                        by_id[entry.id].vector_score = entry.vector_score
                    else:
                        entries.append(entry)
                        seen.add(entry.id)
                entries = rank_entries_with_decay(entries, self._recall_limit)

        if entries:
            async with self._uow_factory() as uow:
                await uow.memory_entry.touch_used([e.id for e in entries])
        if not entries:
            return ""
        lines = []
        for e in entries:
            tags_str = ",".join(e.tags) if e.tags else ""
            lines.append(f"- [{tags_str}] {e.title}: {e.content}")
        return "<long_term_memory>\n" + "\n".join(lines) + "\n</long_term_memory>"

    async def save_from_tool(
            self,
            title: str,
            content: str,
            tags: List[str],
            scope: str,
            session_id: Optional[str],
    ) -> MemoryEntry:
        entry = MemoryEntry(
            title=title,
            content=content,
            tags=tags or [],
            scope=MemoryScope(scope) if scope in ("global", "session") else MemoryScope.GLOBAL,
            session_id=session_id if scope == "session" else None,
            source=MemorySource.TOOL_SAVE,
        )
        return await self.create_entry(entry)
