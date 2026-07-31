#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Chunk and embed codebase content for semantic search."""
import logging
import uuid
from typing import Dict, List, Optional

from app.domain.models.codebase import CodebaseChunk, CodebaseFile, CodebaseSymbol
from app.domain.services.codebase.lexical_indexer import CodebaseLexicalIndexer
from app.domain.services.codebase.vector_service import CodebaseVectorService

CHUNK_MAX_CHARS = 2000
logger = logging.getLogger(__name__)


class CodebaseIndexer:
    def __init__(
            self,
            vector_service: Optional[CodebaseVectorService] = None,
            lexical_indexer: Optional[CodebaseLexicalIndexer] = None,
    ) -> None:
        self._vector = vector_service or CodebaseVectorService()
        self._lexical = lexical_indexer or CodebaseLexicalIndexer()

    async def build_chunks(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            file_contents: Dict[str, str],
            version_id: Optional[str] = None,
    ) -> List[CodebaseChunk]:
        chunks: List[CodebaseChunk] = []
        pending_texts: List[str] = []
        pending_meta: List[tuple[str, Optional[str], str, str, str]] = []
        path_by_file_id = {f.id: f.path for f in files}

        def queue_chunk(
                *,
                file_id: str,
                symbol_id: Optional[str],
                chunk_text: str,
                search_text: str,
        ) -> None:
            pending_texts.append(chunk_text)
            pending_meta.append(
                (file_id, symbol_id, chunk_text, search_text, str(uuid.uuid4()))
            )

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
            search_text = self._lexical.search_text(
                path=path,
                symbols=[
                    sym.name,
                    sym.qualified_name,
                    sym.signature,
                    sym.kind.value,
                ],
                content=snippet,
            )
            queue_chunk(
                file_id=sym.file_id,
                symbol_id=sym.id,
                chunk_text=chunk_text,
                search_text=search_text,
            )

        covered_file_ids = {meta[0] for meta in pending_meta}
        for f in files:
            if f.id in covered_file_ids:
                continue
            content = file_contents.get(f.path, "")
            if not content.strip():
                continue
            header = f"File: {f.path}\nLanguage: {f.language}\n"
            chunk_text = header + content[:CHUNK_MAX_CHARS]
            search_text = self._lexical.search_text(
                path=f.path,
                symbols=[f.language],
                content=content,
            )
            queue_chunk(
                file_id=f.id,
                symbol_id=None,
                chunk_text=chunk_text,
                search_text=search_text,
            )

        embeddings: List[List[float]] = []
        if pending_texts and self._vector.enabled:
            try:
                embeddings = await self._vector.embed_batch(pending_texts)
            except Exception:
                logger.warning(
                    "代码库 embedding 批量生成失败，保留 lexical-only chunks",
                    exc_info=True,
                )
                embeddings = [[] for _ in pending_texts]
        elif pending_texts:
            embeddings = [[] for _ in pending_texts]

        for index, (
            file_id,
            symbol_id,
            chunk_text,
            search_text,
            chunk_id,
        ) in enumerate(pending_meta):
            embedding = embeddings[index] if index < len(embeddings) else []
            chunks.append(
                CodebaseChunk(
                    id=chunk_id,
                    codebase_id=codebase_id,
                    version_id=version_id,
                    file_id=file_id,
                    symbol_id=symbol_id,
                    content=chunk_text,
                    search_text=search_text or chunk_text,
                    embedding=embedding,
                )
            )
        return chunks
