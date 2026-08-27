from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.memory_entry_repository import MemoryEntryRepository
from app.infrastructure.models.memory_entry import MemoryEntryORM
from app.infrastructure.models.session import SessionModel


class DBMemoryEntryRepository(MemoryEntryRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _scope_conditions(self, owner_scope: OwnerScope | None):
        if owner_scope is None:
            return []
        if owner_scope.type == OwnerScopeType.TEAM:
            return [MemoryEntryORM.team_id == owner_scope.team_id]
        return [
            MemoryEntryORM.owner_user_id == owner_scope.user_id,
            MemoryEntryORM.team_id.is_(None),
        ]

    async def get_all(
        self,
        scope: MemoryScope | None = None,
        session_id: str | None = None,
        q: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        owner_scope: OwnerScope | None = None,
    ) -> list[MemoryEntry]:
        stmt = (
            select(MemoryEntryORM)
            .order_by(
                MemoryEntryORM.last_used_at.desc().nullslast(),
                MemoryEntryORM.created_at.desc(),
            )
            .limit(limit)
        )
        conditions = self._scope_conditions(owner_scope)
        if scope:
            conditions.append(MemoryEntryORM.scope == scope.value)
        if session_id:
            conditions.append(MemoryEntryORM.session_id == session_id)
        if q:
            conditions.append(MemoryEntryORM.title.ilike(f"%{q}%"))
        if tags:
            conditions.append(MemoryEntryORM.tags.op("?|")(array(tags)))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_by_id(
        self, entry_id: str, owner_scope: OwnerScope | None = None
    ) -> MemoryEntry | None:
        stmt = select(MemoryEntryORM).where(
            MemoryEntryORM.id == entry_id, *self._scope_conditions(owner_scope)
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def recall_for_session(self, session_id: str, limit: int = 20) -> list[MemoryEntry]:
        fetch_limit = max(limit * 3, limit)
        session = await self.db_session.scalar(
            select(SessionModel).where(SessionModel.id == session_id).limit(1)
        )
        if session is None:
            return []
        if session.team_id:
            owner_conditions = [MemoryEntryORM.team_id == session.team_id]
        else:
            owner_conditions = [
                MemoryEntryORM.owner_user_id == session.owner_user_id,
                MemoryEntryORM.team_id.is_(None),
            ]
        stmt = (
            select(MemoryEntryORM)
            .where(
                and_(
                    *owner_conditions,
                    or_(
                        MemoryEntryORM.scope == MemoryScope.GLOBAL.value,
                        and_(
                            MemoryEntryORM.scope == MemoryScope.SESSION.value,
                            MemoryEntryORM.session_id == session_id,
                        ),
                    ),
                )
            )
            .order_by(
                MemoryEntryORM.last_used_at.desc().nullslast(),
                MemoryEntryORM.use_count.desc(),
                MemoryEntryORM.created_at.desc(),
            )
            .limit(fetch_limit)
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def save(self, entry: MemoryEntry) -> None:
        stmt = select(MemoryEntryORM).where(MemoryEntryORM.id == entry.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        entry.updated_at = datetime.now(UTC)
        if record:
            record.scope = entry.scope.value
            record.session_id = entry.session_id
            record.title = entry.title
            record.content = entry.content
            record.tags = entry.tags
            record.owner_user_id = entry.owner_user_id
            record.team_id = entry.team_id
            record.source = entry.source.value
            record.last_used_at = entry.last_used_at
            record.use_count = entry.use_count
            record.updated_at = entry.updated_at
        else:
            self.db_session.add(MemoryEntryORM.from_domain(entry))

    async def delete_by_id(self, entry_id: str, owner_scope: OwnerScope | None = None) -> None:
        await self.db_session.execute(
            delete(MemoryEntryORM).where(
                MemoryEntryORM.id == entry_id, *self._scope_conditions(owner_scope)
            )
        )

    async def touch_used(self, entry_ids: list[str]) -> None:
        if not entry_ids:
            return
        await self.db_session.execute(
            update(MemoryEntryORM)
            .where(MemoryEntryORM.id.in_(entry_ids))
            .values(
                last_used_at=datetime.now(UTC),
                use_count=MemoryEntryORM.use_count + 1,
            )
        )

    async def update_embedding(self, entry_id: str, embedding: list[float]) -> None:
        stmt = (
            update(MemoryEntryORM).where(MemoryEntryORM.id == entry_id).values(embedding=embedding)
        )
        await self.db_session.execute(stmt)

    async def vector_search_entries(
        self,
        query_embedding: list[float],
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        # 向量距离检索仍使用 pgvector 运算符（ORM 不直接支持 <=>）
        stmt = text("""
            SELECT id, scope, session_id, title, content, tags, owner_user_id,
                   team_id, source, last_used_at, use_count, created_at, updated_at,
                   embedding <=> CAST(:query_vec AS vector) AS distance
            FROM memory_entries
            WHERE embedding IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM sessions s
                WHERE s.id = :session_id
                  AND (
                    (s.team_id IS NOT NULL AND memory_entries.team_id = s.team_id)
                    OR (
                      s.team_id IS NULL
                      AND memory_entries.team_id IS NULL
                      AND memory_entries.owner_user_id = s.owner_user_id
                    )
                  )
              )
              AND (
                scope = 'global'
                OR (scope = 'session' AND session_id = :session_id)
              )
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :limit
        """)
        params = {
            "session_id": session_id,
            "query_vec": "[" + ",".join(str(v) for v in query_embedding) + "]",
            "limit": limit,
        }
        result = await self.db_session.execute(stmt, params)
        rows = result.fetchall()

        return [
            MemoryEntry(
                id=row.id,
                scope=MemoryScope(row.scope),
                session_id=row.session_id,
                title=row.title,
                content=row.content,
                tags=row.tags or [],
                owner_user_id=row.owner_user_id,
                team_id=row.team_id,
                source=MemorySource(row.source),
                last_used_at=row.last_used_at,
                use_count=row.use_count,
                vector_score=max(0.0, 1.0 - float(row.distance or 0.0)),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
