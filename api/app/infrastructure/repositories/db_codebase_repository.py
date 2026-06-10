#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Tuple

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.codebase import (
    ArtifactKind,
    Codebase,
    CodebaseArtifact,
    CodebaseChunk,
    CodebaseEdge,
    CodebaseFile,
    CodebaseStatus,
    CodebaseSymbol,
)
from app.domain.repositories.codebase_repository import CodebaseRepository
from app.infrastructure.models.codebase import (
    CodebaseArtifactModel,
    CodebaseChunkModel,
    CodebaseEdgeModel,
    CodebaseFileModel,
    CodebaseModel,
    CodebaseSymbolModel,
)


class DBCodebaseRepository(CodebaseRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, codebase: Codebase) -> None:
        stmt = select(CodebaseModel).where(CodebaseModel.id == codebase.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(CodebaseModel.from_domain(codebase))
            return
        record.name = codebase.name
        record.source_type = codebase.source_type.value
        record.source_ref = codebase.source_ref
        record.status = codebase.status.value
        record.language_stats = codebase.language_stats
        record.file_count = codebase.file_count
        record.sandbox_id = codebase.sandbox_id
        record.workspace_path = codebase.workspace_path
        record.snapshot_key = codebase.snapshot_key
        record.ingest_task_id = codebase.ingest_task_id
        record.error = codebase.error
        record.updated_at = codebase.updated_at

    async def get_by_id(self, codebase_id: str) -> Optional[Codebase]:
        stmt = select(CodebaseModel).where(CodebaseModel.id == codebase_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Codebase]:
        stmt = (
            select(CodebaseModel)
            .order_by(CodebaseModel.updated_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def delete_by_id(self, codebase_id: str) -> None:
        await self.db_session.execute(delete(CodebaseModel).where(CodebaseModel.id == codebase_id))

    async def update_status(
            self,
            codebase_id: str,
            status: CodebaseStatus,
            error: Optional[str] = None,
    ) -> None:
        values = {"status": status.value}
        if error is not None:
            values["error"] = error
        await self.db_session.execute(
            update(CodebaseModel).where(CodebaseModel.id == codebase_id).values(**values)
        )

    async def save_files(self, files: List[CodebaseFile]) -> None:
        for f in files:
            self.db_session.add(
                CodebaseFileModel(
                    id=f.id,
                    codebase_id=f.codebase_id,
                    path=f.path,
                    language=f.language,
                    size=f.size,
                    sha=f.sha,
                )
            )

    async def list_files(self, codebase_id: str) -> List[CodebaseFile]:
        stmt = (
            select(CodebaseFileModel)
            .where(CodebaseFileModel.codebase_id == codebase_id)
            .order_by(CodebaseFileModel.path.asc())
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def get_file_by_path(self, codebase_id: str, path: str) -> Optional[CodebaseFile]:
        stmt = (
            select(CodebaseFileModel)
            .where(CodebaseFileModel.codebase_id == codebase_id)
            .where(CodebaseFileModel.path == path)
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save_symbols(self, symbols: List[CodebaseSymbol]) -> None:
        for s in symbols:
            self.db_session.add(
                CodebaseSymbolModel(
                    id=s.id,
                    codebase_id=s.codebase_id,
                    file_id=s.file_id,
                    name=s.name,
                    kind=s.kind.value,
                    signature=s.signature,
                    start_line=s.start_line,
                    end_line=s.end_line,
                    parent_id=s.parent_id,
                )
            )

    async def list_symbols(self, codebase_id: str, name: Optional[str] = None) -> List[CodebaseSymbol]:
        stmt = select(CodebaseSymbolModel).where(CodebaseSymbolModel.codebase_id == codebase_id)
        if name:
            stmt = stmt.where(CodebaseSymbolModel.name.ilike(f"%{name}%"))
        stmt = stmt.order_by(CodebaseSymbolModel.name.asc())
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def find_symbol_by_name(self, codebase_id: str, name: str) -> List[CodebaseSymbol]:
        stmt = (
            select(CodebaseSymbolModel)
            .where(CodebaseSymbolModel.codebase_id == codebase_id)
            .where(CodebaseSymbolModel.name == name)
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def save_edges(self, edges: List[CodebaseEdge]) -> None:
        for e in edges:
            self.db_session.add(
                CodebaseEdgeModel(
                    id=e.id,
                    codebase_id=e.codebase_id,
                    src_symbol_id=e.src_symbol_id,
                    dst_symbol_id=e.dst_symbol_id,
                    callee_name=e.callee_name,
                    kind=e.kind.value,
                )
            )

    async def list_edges(
            self,
            codebase_id: str,
            src_symbol_id: Optional[str] = None,
            dst_symbol_id: Optional[str] = None,
            callee_name: Optional[str] = None,
    ) -> List[CodebaseEdge]:
        stmt = select(CodebaseEdgeModel).where(CodebaseEdgeModel.codebase_id == codebase_id)
        if src_symbol_id:
            stmt = stmt.where(CodebaseEdgeModel.src_symbol_id == src_symbol_id)
        if dst_symbol_id:
            stmt = stmt.where(CodebaseEdgeModel.dst_symbol_id == dst_symbol_id)
        if callee_name:
            stmt = stmt.where(CodebaseEdgeModel.callee_name == callee_name)
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def list_symbols_by_ids(
            self,
            codebase_id: str,
            symbol_ids: List[str],
    ) -> List[CodebaseSymbol]:
        if not symbol_ids:
            return []
        stmt = (
            select(CodebaseSymbolModel)
            .where(CodebaseSymbolModel.codebase_id == codebase_id)
            .where(CodebaseSymbolModel.id.in_(symbol_ids))
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def save_chunks(self, chunks: List[CodebaseChunk]) -> None:
        for chunk in chunks:
            if chunk.embedding:
                await self.db_session.execute(
                    text(
                        """
                        INSERT INTO codebase_chunks
                            (id, codebase_id, file_id, symbol_id, content, embedding)
                        VALUES
                            (:id, :codebase_id, :file_id, :symbol_id, :content, :embedding::vector)
                        """
                    ),
                    {
                        "id": chunk.id,
                        "codebase_id": chunk.codebase_id,
                        "file_id": chunk.file_id,
                        "symbol_id": chunk.symbol_id,
                        "content": chunk.content,
                        "embedding": str(chunk.embedding),
                    },
                )
            else:
                self.db_session.add(
                    CodebaseChunkModel(
                        id=chunk.id,
                        codebase_id=chunk.codebase_id,
                        file_id=chunk.file_id,
                        symbol_id=chunk.symbol_id,
                        content=chunk.content,
                    )
                )

    async def search_chunks(
            self,
            codebase_id: str,
            query_embedding: List[float],
            limit: int = 10,
    ) -> List[Tuple[CodebaseChunk, float]]:
        if not query_embedding:
            return []
        stmt = text(
            """
            SELECT id, codebase_id, file_id, symbol_id, content,
                   1 - (embedding <=> :query::vector) AS score
            FROM codebase_chunks
            WHERE codebase_id = :codebase_id AND embedding IS NOT NULL
            ORDER BY embedding <=> :query::vector
            LIMIT :limit
            """
        )
        result = await self.db_session.execute(
            stmt,
            {
                "query": str(query_embedding),
                "codebase_id": codebase_id,
                "limit": limit,
            },
        )
        rows = result.fetchall()
        out: List[Tuple[CodebaseChunk, float]] = []
        for row in rows:
            chunk = CodebaseChunk(
                id=row.id,
                codebase_id=row.codebase_id,
                file_id=row.file_id,
                symbol_id=row.symbol_id,
                content=row.content or "",
            )
            out.append((chunk, float(row.score or 0)))
        return out

    async def save_artifacts(self, artifacts: List[CodebaseArtifact]) -> None:
        for a in artifacts:
            self.db_session.add(
                CodebaseArtifactModel(
                    id=a.id,
                    codebase_id=a.codebase_id,
                    kind=a.kind.value,
                    format=a.format.value,
                    title=a.title,
                    content=a.content,
                    meta=a.meta,
                    created_at=a.created_at,
                )
            )

    async def list_artifacts(
            self,
            codebase_id: str,
            kind: Optional[ArtifactKind] = None,
    ) -> List[CodebaseArtifact]:
        stmt = select(CodebaseArtifactModel).where(CodebaseArtifactModel.codebase_id == codebase_id)
        if kind:
            stmt = stmt.where(CodebaseArtifactModel.kind == kind.value)
        stmt = stmt.order_by(CodebaseArtifactModel.created_at.asc())
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def clear_analysis_data(self, codebase_id: str) -> None:
        await self.db_session.execute(
            delete(CodebaseArtifactModel).where(CodebaseArtifactModel.codebase_id == codebase_id)
        )
        await self.db_session.execute(
            delete(CodebaseChunkModel).where(CodebaseChunkModel.codebase_id == codebase_id)
        )
        await self.db_session.execute(
            delete(CodebaseEdgeModel).where(CodebaseEdgeModel.codebase_id == codebase_id)
        )
        await self.db_session.execute(
            delete(CodebaseSymbolModel).where(CodebaseSymbolModel.codebase_id == codebase_id)
        )
        await self.db_session.execute(
            delete(CodebaseFileModel).where(CodebaseFileModel.codebase_id == codebase_id)
        )
