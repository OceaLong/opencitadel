import logging
from collections.abc import Callable

from app.domain.errors import BadRequestError, NotFoundError
from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import MemoryExecutionPolicy
from app.domain.utils.memory_recall import rank_entries_with_decay
from app.domain.vector_port import EmbeddingPort

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        embeddings: EmbeddingPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._embeddings = embeddings

    def _apply_owner_scope(self, entry: MemoryEntry, owner_scope: OwnerScope | None) -> None:
        if owner_scope is None:
            return
        entry.owner_user_id = owner_scope.user_id
        entry.team_id = owner_scope.team_id if owner_scope.type == OwnerScopeType.TEAM else None

    async def _validate_entry(
        self, entry: MemoryEntry, owner_scope: OwnerScope | None = None
    ) -> None:
        if entry.scope == MemoryScope.SESSION:
            if not entry.session_id:
                raise BadRequestError("scope=session 时必须提供 session_id")
            async with self._uow_factory() as uow:
                session = await uow.session.get_by_id(entry.session_id, scope=owner_scope)
            if not session:
                raise NotFoundError(f"会话[{entry.session_id}]不存在")
            if owner_scope is None:
                entry.owner_user_id = session.owner_user_id
                entry.team_id = session.team_id
        elif entry.session_id:
            entry.session_id = None
        self._apply_owner_scope(entry, owner_scope)

    async def list_entries(
        self,
        scope: MemoryScope | None = None,
        session_id: str | None = None,
        q: str | None = None,
        tags: list[str] | None = None,
        owner_scope: OwnerScope | None = None,
    ) -> list[MemoryEntry]:
        async with self._uow_factory() as uow:
            return await uow.memory_entry.get_all(
                scope=scope,
                session_id=session_id,
                q=q,
                tags=tags,
                owner_scope=owner_scope,
            )

    async def get_entry(self, entry_id: str, owner_scope: OwnerScope | None = None) -> MemoryEntry:
        async with self._uow_factory() as uow:
            entry = await uow.memory_entry.get_by_id(entry_id, owner_scope=owner_scope)
        if not entry:
            raise NotFoundError(f"记忆[{entry_id}]不存在")
        return entry

    async def create_entry(
        self,
        entry: MemoryEntry,
        owner_scope: OwnerScope | None = None,
        *,
        policy: MemoryExecutionPolicy,
    ) -> MemoryEntry:
        await self._validate_entry(entry, owner_scope=owner_scope)
        async with self._uow_factory() as uow:
            await uow.memory_entry.save(entry)
            vector = await self._embed(
                f"{entry.title}\n{entry.content}",
                owner_scope or self._entry_scope(entry),
                policy=policy,
                purpose_context="memory.store",
            )
            if vector:
                await uow.memory_entry.update_embedding(entry.id, vector)
            await uow.commit()
        return entry

    async def update_entry(
        self,
        entry_id: str,
        updates: MemoryEntry,
        owner_scope: OwnerScope | None = None,
        *,
        policy: MemoryExecutionPolicy,
    ) -> MemoryEntry:
        await self._validate_entry(updates, owner_scope=owner_scope)
        async with self._uow_factory() as uow:
            existing = await uow.memory_entry.get_by_id(entry_id, owner_scope=owner_scope)
            if not existing:
                raise NotFoundError(f"记忆[{entry_id}]不存在")
            updates.id = entry_id
            await uow.memory_entry.save(updates)
            vector = await self._embed(
                f"{updates.title}\n{updates.content}",
                owner_scope or self._entry_scope(updates),
                policy=policy,
                purpose_context="memory.store",
            )
            if vector:
                await uow.memory_entry.update_embedding(entry_id, vector)
            await uow.commit()
        return updates

    async def delete_entry(self, entry_id: str, owner_scope: OwnerScope | None = None) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.memory_entry.get_by_id(entry_id, owner_scope=owner_scope)
            if not existing:
                raise NotFoundError(f"记忆[{entry_id}]不存在")
            await uow.memory_entry.delete_by_id(entry_id, owner_scope=owner_scope)
            await uow.commit()

    async def recall_for_session(
        self,
        session_id: str,
        *,
        owner_scope: OwnerScope,
        policy: MemoryExecutionPolicy,
    ) -> str:
        """召回长期记忆并格式化为system块（时间衰减 + 可选向量混合检索）"""
        query_text = ""
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=owner_scope)
            if session is None:
                raise NotFoundError(f"会话[{session_id}]不存在")
            query_text = session.latest_message or ""
            entries = await uow.memory_entry.recall_for_session(
                session_id, limit=policy.recall_limit
            )
            entries = rank_entries_with_decay(entries, policy.recall_limit)

            if policy.vector_enabled and query_text.strip():
                query_vector = await self._embed(
                    query_text,
                    owner_scope,
                    policy=policy,
                    purpose_context="memory.query",
                )
                vector_entries = (
                    await uow.memory_entry.vector_search_entries(
                        query_vector,
                        session_id=session_id,
                        limit=policy.recall_limit,
                    )
                    if query_vector
                    else []
                )
                by_id = {e.id: e for e in entries}
                seen = set(by_id.keys())
                for entry in vector_entries:
                    if entry.id in by_id:
                        by_id[entry.id].vector_score = entry.vector_score
                    else:
                        entries.append(entry)
                        seen.add(entry.id)
                entries = rank_entries_with_decay(entries, policy.recall_limit)

        if entries:
            async with self._uow_factory() as uow:
                await uow.memory_entry.touch_used([e.id for e in entries])
                await uow.commit()
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
        tags: list[str],
        scope: str,
        session_id: str | None,
        *,
        policy: MemoryExecutionPolicy,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            title=title,
            content=content,
            tags=tags or [],
            scope=MemoryScope(scope) if scope in ("global", "session") else MemoryScope.GLOBAL,
            session_id=session_id if scope == "session" else None,
            source=MemorySource.TOOL_SAVE,
        )
        if session_id:
            async with self._uow_factory() as uow:
                session = await uow.session.get_metadata(session_id)
            if session:
                entry.owner_user_id = session.owner_user_id
                entry.team_id = session.team_id
        return await self.create_entry(entry, policy=policy)

    async def _embed(
        self,
        content: str,
        scope: OwnerScope | None,
        *,
        policy: MemoryExecutionPolicy,
        purpose_context: str,
    ) -> list[float]:
        if not policy.vector_enabled or not content.strip():
            return []
        if self._embeddings is None:
            raise RuntimeError("memory vector search requires EmbeddingService")
        vectors = await self._embeddings.embed(
            [content],
            scope=scope,
            purpose_context=purpose_context,
        )
        return vectors[0] if vectors else []

    @staticmethod
    def _entry_scope(entry: MemoryEntry) -> OwnerScope | None:
        if entry.team_id:
            return OwnerScope.team(entry.owner_user_id or "memory", entry.team_id)
        if entry.owner_user_id:
            return OwnerScope.personal(entry.owner_user_id)
        return None
