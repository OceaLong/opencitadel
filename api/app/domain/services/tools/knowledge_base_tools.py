#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Knowledge-base retrieval tools for Ask/Agent modes."""
from typing import Callable, Optional
from urllib.parse import urlencode

from app.application.services.config_provider import get_runtime_config
from app.domain.external.llm import LLM
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.rerank_service import RerankSettings
from app.domain.services.knowledge_base.retriever import HybridRetriever, RetrievalSettings
from app.domain.services.tools.base import BaseTool, tool


class KnowledgeBaseTool(BaseTool):
    name: str = "knowledge_base"

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            kb_id: str,
            llm: Optional[LLM] = None,
    ) -> None:
        super().__init__()
        self._uow_factory = uow_factory
        self._kb_id = kb_id
        self._llm = llm

    @tool(
        name="kb_search",
        description="检索企业文档知识库，返回带可点击引用来源的相关文档片段",
        parameters={
            "query": {"type": "string", "description": "搜索查询"},
            "limit": {"type": "integer", "description": "返回结果数量，默认5"},
        },
        required=["query"],
    )
    async def kb_search(self, query: str, limit: int = 5) -> str:
        runtime = get_runtime_config().knowledge_base
        retriever = HybridRetriever(
            uow_factory=self._uow_factory,
            llm=self._llm,
            settings=RetrievalSettings(
                vector_top_k=runtime.retrieval.vector_top_k,
                bm25_top_k=runtime.retrieval.bm25_top_k,
                rrf_k=runtime.retrieval.rrf_k,
                final_top_k=max(1, min(limit, runtime.retrieval.final_top_k)),
                graph_enabled=runtime.graphrag.enabled,
            ),
            rerank_settings=RerankSettings(
                enabled=runtime.rerank.enabled,
                provider=runtime.rerank.provider,
                timeout_seconds=runtime.rerank.timeout_seconds,
            ),
        )
        results = await retriever.retrieve(self._kb_id, query, top_k=limit)
        if not results:
            return "未找到相关文档片段"
        lines = []
        for item in results:
            chunk = item.chunk
            doc = item.document
            query_string = urlencode({
                "page": chunk.page_no or "",
                "chunk": (item.parent.id if item.parent else chunk.id),
            })
            href = f"kbdoc://{doc.id}?{query_string}"
            title = f"《{doc.title}》p{chunk.page_no or '?'}"
            if chunk.heading_path:
                title += f"·{chunk.heading_path}"
            lines.append(
                f"[score={item.score:.3f}] [{title}]({href})\n"
                f"{item.content[:1200]}"
            )
        return "\n\n---\n\n".join(lines)

    @tool(
        name="graph_search",
        description="按实体名称搜索知识图谱关系",
        parameters={
            "entity": {"type": "string", "description": "实体名称"},
        },
        required=["entity"],
    )
    async def graph_search(self, entity: str) -> str:
        async with self._uow_factory() as uow:
            entities = await uow.knowledge_base.list_entities(self._kb_id, name=entity)
            relations = await uow.knowledge_base.list_relations_for_entities(
                self._kb_id,
                [item.id for item in entities],
            )
        if not entities:
            return f"未找到实体: {entity}"
        entity_by_id = {item.id: item for item in entities}
        lines = [f"## 实体: {item.name} ({item.type})\n{item.description}" for item in entities[:10]]
        if relations:
            lines.append("## 关系")
        for relation in relations[:30]:
            src = entity_by_id.get(relation.src_entity_id)
            dst = entity_by_id.get(relation.dst_entity_id)
            lines.append(
                f"- {(src.name if src else relation.src_entity_id)} --{relation.relation}--> "
                f"{(dst.name if dst else relation.dst_entity_id)}"
            )
        return "\n".join(lines)

    @tool(
        name="get_document",
        description="读取指定文档的原文片段，可按页码过滤",
        parameters={
            "doc_id": {"type": "string", "description": "文档ID"},
            "page": {"type": "integer", "description": "页码，可选"},
        },
        required=["doc_id"],
    )
    async def get_document(self, doc_id: str, page: Optional[int] = None) -> str:
        async with self._uow_factory() as uow:
            doc = await uow.knowledge_base.get_document(doc_id)
            if not doc:
                return f"未找到文档: {doc_id}"
            if doc.kb_id != self._kb_id:
                return f"文档 {doc_id} 不属于当前知识库"
            chunks = await uow.knowledge_base.list_chunks_for_document(doc_id, page_no=page, limit=30)
        if not chunks:
            return f"文档《{doc.title}》暂无可读取片段"
        lines = [f"# 《{doc.title}》"]
        for chunk in chunks:
            if chunk.level.value != "parent":
                continue
            lines.append(f"## p{chunk.page_no or '?'} {chunk.heading_path}\n{chunk.content}")
        return "\n\n".join(lines[:20])
