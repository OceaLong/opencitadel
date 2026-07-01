#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Parent/child chunking for enterprise document RAG."""
import uuid
from dataclasses import dataclass
from typing import List, Tuple

from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.services.knowledge_base.parsers import PageBlock
from app.domain.services.knowledge_base.vector_service import KBVectorService
from app.domain.services.knowledge_base.zh_tokenizer import segment_for_bm25


@dataclass
class ChunkSettings:
    parent_max_chars: int = 2000
    child_max_chars: int = 400
    overlap: int = 50


class KBChunker:
    def __init__(
            self,
            vector_service: KBVectorService | None = None,
            settings: ChunkSettings | None = None,
    ) -> None:
        self._vector = vector_service or KBVectorService()
        self._settings = settings or ChunkSettings()

    async def build_chunks(
            self,
            kb_id: str,
            doc_id: str,
            blocks: List[PageBlock],
    ) -> Tuple[List[KnowledgeChunk], List[KnowledgeChunk]]:
        parents = self._build_parent_chunks(kb_id, doc_id, blocks)
        children = self._build_child_chunks(kb_id, doc_id, parents)
        embeddings = await self._vector.embed_batch([chunk.content for chunk in children])
        for chunk, embedding in zip(children, embeddings):
            chunk.embedding = embedding
        return parents, children

    def _build_parent_chunks(
            self,
            kb_id: str,
            doc_id: str,
            blocks: List[PageBlock],
    ) -> List[KnowledgeChunk]:
        parents: list[KnowledgeChunk] = []
        ordinal = 0
        for block in blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            for part in _split_by_size(text, self._settings.parent_max_chars, 0):
                parent = KnowledgeChunk(
                    id=str(uuid.uuid4()),
                    kb_id=kb_id,
                    doc_id=doc_id,
                    level=ChunkLevel.PARENT,
                    content=_with_header(block.heading_path, block.page_no, part),
                    segmented_content=segment_for_bm25(part),
                    page_no=block.page_no,
                    heading_path=block.heading_path,
                    ordinal=ordinal,
                )
                parents.append(parent)
                ordinal += 1
        return parents

    def _build_child_chunks(
            self,
            kb_id: str,
            doc_id: str,
            parents: List[KnowledgeChunk],
    ) -> List[KnowledgeChunk]:
        children: list[KnowledgeChunk] = []
        ordinal = 0
        for parent in parents:
            for part in _split_by_size(
                _strip_header(parent.content),
                self._settings.child_max_chars,
                self._settings.overlap,
            ):
                child = KnowledgeChunk(
                    id=str(uuid.uuid4()),
                    kb_id=kb_id,
                    doc_id=doc_id,
                    parent_id=parent.id,
                    level=ChunkLevel.CHILD,
                    content=_with_header(parent.heading_path, parent.page_no or 1, part),
                    segmented_content=segment_for_bm25(part),
                    page_no=parent.page_no,
                    heading_path=parent.heading_path,
                    ordinal=ordinal,
                )
                children.append(child)
                ordinal += 1
        return children


def _split_by_size(text: str, max_chars: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        # Prefer paragraph boundaries when close enough.
        boundary = text.rfind("\n\n", start, end)
        if boundary > start + max_chars // 2:
            end = boundary
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _with_header(heading_path: str, page_no: int, content: str) -> str:
    return f"Source: {heading_path or '文档'}\nPage: {page_no}\n\n{content}".strip()


def _strip_header(content: str) -> str:
    marker = "\n\n"
    if marker in content and content.startswith("Source:"):
        return content.split(marker, 1)[1]
    return content
