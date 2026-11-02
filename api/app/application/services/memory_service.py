import logging
from collections.abc import Callable

from app.domain.errors import BadRequestError, NotFoundError
from app.domain.execution.context_models import context_digest
from app.domain.execution.context_resources import ContextMemoryRecall, ContextMemoryVersion
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
        vector, space_digest = await self._embed(
            f"{entry.title}\n{entry.content}",
            owner_scope or self._entry_scope(entry),
            policy=policy,
            purpose_context="memory.store",
        )
        await self._validate_entry(entry, owner_scope=owner_scope)
        async with self._uow_factory() as uow:
            await uow.memory_entry.save(entry)
            await uow.memory_entry.update_embedding(
                entry.id, vector or None, embedding_space_digest=space_digest
            )
            entry.embedding_space_digest = space_digest
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
        vector, space_digest = await self._embed(
            f"{updates.title}\n{updates.content}",
            owner_scope or self._entry_scope(updates),
            policy=policy,
            purpose_context="memory.store",
        )
        await self._validate_entry(updates, owner_scope=owner_scope)
        async with self._uow_factory() as uow:
            current = await uow.memory_entry.get_by_id(entry_id, owner_scope=owner_scope)
            if not current:
                raise NotFoundError(f"记忆[{entry_id}]不存在")
            updates.id = entry_id
            await uow.memory_entry.save(
                updates, expected_updated_at=existing.updated_at, owner_scope=owner_scope
            )
            await uow.memory_entry.update_embedding(
                entry_id, vector or None, embedding_space_digest=space_digest
            )
            updates.embedding_space_digest = space_digest
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
        """Compatibility entry point for callers without an accepted query cut."""
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=owner_scope)
            if session is None:
                raise NotFoundError(f"会话[{session_id}]不存在")
            query_text = session.latest_message or ""
        recall = await self.recall_context(
            session_id, query=query_text, owner_scope=owner_scope, policy=policy
        )
        return recall.content

    async def recall_context(
        self,
        session_id: str,
        *,
        query: str,
        owner_scope: OwnerScope,
        policy: MemoryExecutionPolicy,
    ) -> ContextMemoryRecall:
        """Bind retrieval to the accepted input and each selected content revision."""
        async with self._uow_factory() as uow:
            if await uow.session.get_by_id(session_id, scope=owner_scope) is None:
                raise NotFoundError(f"会话[{session_id}]不存在")
        query_vector, space_digest = (
            await self._embed(query, owner_scope, policy=policy, purpose_context="memory.query")
            if policy.vector_enabled and query.strip()
            else ([], None)
        )
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=owner_scope)
            if session is None:
                raise NotFoundError(f"会话[{session_id}]不存在")
            entries = await uow.memory_entry.recall_for_session(
                session_id, limit=policy.recall_limit
            )
            entries = rank_entries_with_decay(entries, policy.recall_limit)

            if query_vector:
                vector_entries = (
                    await uow.memory_entry.vector_search_entries(
                        query_vector,
                        session_id=session_id,
                        limit=policy.recall_limit,
                        embedding_space_digest=space_digest,
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
        lines = []
        for e in entries:
            tags_str = ",".join(e.tags) if e.tags else ""
            lines.append(f"- [{tags_str}] {e.title}: {e.content}")
        return ContextMemoryRecall(
            query_digest=context_digest(query),
            query_embedding_space_digest=space_digest,
            content="<long_term_memory>\n" + "\n".join(lines) + "\n</long_term_memory>"
            if entries
            else "",
            entries=tuple(ContextMemoryVersion.from_entry(entry) for entry in entries),
        )

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
    ) -> tuple[list[float], str | None]:
        if not policy.vector_enabled or not content.strip():
            return [], None
        if self._embeddings is None:
            raise RuntimeError("memory vector search requires EmbeddingService")
        space = await self._embeddings.resolve_space(scope=scope)
        vectors = await self._embeddings.embed(
            [content],
            scope=scope,
            purpose_context=purpose_context,
            expected_space=space,
        )
        return (vectors[0], space.digest) if vectors and vectors[0] else ([], None)

    @staticmethod
    def _entry_scope(entry: MemoryEntry) -> OwnerScope | None:
        if entry.team_id:
            return OwnerScope.team(entry.owner_user_id or "memory", entry.team_id)
        if entry.owner_user_id:
            return OwnerScope.personal(entry.owner_user_id)
        return None
