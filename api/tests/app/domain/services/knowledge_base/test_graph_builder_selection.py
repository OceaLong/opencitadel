#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.services.knowledge_base.graph_builder import GraphBuilder


def test_graph_builder_caps_per_document_not_globally():
    builder = GraphBuilder(uow_factory=lambda: None, llm=None, json_parser=None, max_parent_chunks_per_doc=2)
    chunks = []
    for doc_idx in range(2):
        for idx in range(3):
            chunks.append(
                KnowledgeChunk(
                    kb_id="kb1",
                    doc_id=f"doc-{doc_idx}",
                    level=ChunkLevel.PARENT,
                    content=f"doc{doc_idx}-chunk{idx}",
                    ordinal=idx,
                )
            )
    selected = []
    skipped = 0
    from collections import defaultdict

    by_doc: dict[str, list[KnowledgeChunk]] = defaultdict(list)
    for chunk in chunks:
        by_doc[chunk.doc_id].append(chunk)
    for doc_chunks in by_doc.values():
        capped = doc_chunks[: builder._max_parent_chunks_per_doc]
        selected.extend(capped)
        skipped += max(0, len(doc_chunks) - len(capped))
    assert len(selected) == 4
    assert skipped == 2
