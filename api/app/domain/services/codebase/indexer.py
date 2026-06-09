#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Chunk and embed codebase content for semantic search."""
import uuid
from typing import Dict, List, Optional

from app.domain.models.codebase import CodebaseChunk, CodebaseFile, CodebaseSymbol
from app.domain.services.codebase.vector_service import CodebaseVectorService

CHUNK_MAX_CHARS = 2000


class CodebaseIndexer:
    def __init__(self, vector_service: Optional[CodebaseVectorService] = None) -> None:
        self._vector = vector_service or CodebaseVectorService()

    async def build_chunks(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            file_contents: Dict[str, str],
    ) -> List[CodebaseChunk]:
        chunks: List[CodebaseChunk] = []
        path_by_file_id = {f.id: f.path for f in files}

        for sym in symbols:
            path = path_by_file_id.get(sym.file_id, "")
            content = file_contents.get(path, "")
            if not content:
                continue
            lines = content.splitlines()
            start = max(0, sym.start_line - 1)
            end = min(len(lines), sym.end_line)
            snippet = "\n".join(lines[start:end])
            if not snippet.strip():
                continue
            header = f"File: {path}\nSymbol: {sym.name} ({sym.kind.value})\n"
            chunk_text = header + snippet
            if len(chunk_text) > CHUNK_MAX_CHARS:
                chunk_text = chunk_text[:CHUNK_MAX_CHARS]
            embedding = await self._vector.embed(chunk_text) if self._vector.enabled else []
            chunks.append(
                CodebaseChunk(
                    id=str(uuid.uuid4()),
                    codebase_id=codebase_id,
                    file_id=sym.file_id,
                    symbol_id=sym.id,
                    content=chunk_text,
                    embedding=embedding,
                )
            )

        for f in files:
            if any(c.file_id == f.id for c in chunks):
                continue
            content = file_contents.get(f.path, "")
            if not content.strip():
                continue
            header = f"File: {f.path}\nLanguage: {f.language}\n"
            chunk_text = header + content[:CHUNK_MAX_CHARS]
            embedding = await self._vector.embed(chunk_text) if self._vector.enabled else []
            chunks.append(
                CodebaseChunk(
                    id=str(uuid.uuid4()),
                    codebase_id=codebase_id,
                    file_id=f.id,
                    content=chunk_text,
                    embedding=embedding,
                )
            )
        return chunks
