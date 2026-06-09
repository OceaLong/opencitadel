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
        pending_texts: List[str] = []
        pending_meta: List[tuple[str, str, Optional[str], Optional[str]]] = []
        path_by_file_id = {f.id: f.path for f in files}

        def queue_chunk(
                *,
                file_id: str,
                symbol_id: Optional[str],
                chunk_text: str,
        ) -> None:
            pending_texts.append(chunk_text)
            pending_meta.append((file_id, symbol_id, chunk_text, str(uuid.uuid4())))

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
            queue_chunk(file_id=sym.file_id, symbol_id=sym.id, chunk_text=chunk_text)

        covered_file_ids = {meta[0] for meta in pending_meta}
        for f in files:
            if f.id in covered_file_ids:
                continue
            content = file_contents.get(f.path, "")
            if not content.strip():
                continue
            header = f"File: {f.path}\nLanguage: {f.language}\n"
            chunk_text = header + content[:CHUNK_MAX_CHARS]
            queue_chunk(file_id=f.id, symbol_id=None, chunk_text=chunk_text)

        embeddings: List[List[float]] = []
        if pending_texts and self._vector.enabled:
            embeddings = await self._vector.embed_batch(pending_texts)
        elif pending_texts:
            embeddings = [[] for _ in pending_texts]

        for index, (file_id, symbol_id, chunk_text, chunk_id) in enumerate(pending_meta):
            embedding = embeddings[index] if index < len(embeddings) else []
            chunks.append(
                CodebaseChunk(
                    id=chunk_id,
                    codebase_id=codebase_id,
                    file_id=file_id,
                    symbol_id=symbol_id,
                    content=chunk_text,
                    embedding=embedding,
                )
            )
        return chunks
