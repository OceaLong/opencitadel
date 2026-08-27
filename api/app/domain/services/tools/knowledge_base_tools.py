"""Knowledge-base retrieval tools for Ask/Agent modes."""

from collections.abc import Callable
from urllib.parse import urlencode

from app.domain.external.llm import LLM
from app.domain.models.knowledge_citation import (
    KnowledgeCitation,
    deduplicate_citations,
)
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import KnowledgeRetrievalRunPolicy
from app.domain.services.knowledge_base.retriever import HybridRetriever
from app.domain.services.knowledge_base.vector_service import KBVectorService
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import READ_SAFE
from app.domain.vector_port import EmbeddingPort


class KnowledgeBaseTool(BaseTool):
    name: str = "knowledge_base"

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        kb_id: str,
        version_id: str,
        *,
        policy: KnowledgeRetrievalRunPolicy,
        llm: LLM | None = None,
        embeddings: EmbeddingPort | None = None,
        owner_scope: OwnerScope | None = None,
    ) -> None:
        super().__init__()
        self._uow_factory = uow_factory
        self._kb_id = kb_id
        if not str(version_id or "").strip():
            raise ValueError("knowledge-base tool requires a version binding")
        self._version_id = version_id
        self._policy = policy
        self._llm = llm
        self._embeddings = embeddings
        self._owner_scope = owner_scope

    @tool(
        name="kb_search",
        description="检索企业文档知识库，返回带可点击引用来源的相关文档片段",
        parameters={
            "query": {"type": "string", "description": "搜索查询"},
            "limit": {"type": "integer", "description": "返回结果数量，默认5"},
        },
        required=["query"],
        policy=READ_SAFE,
    )
    async def kb_search(self, query: str, limit: int | None = None) -> ToolResult[str]:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer")
        effective_limit = min(
            limit or self._policy.retrieval.final_top_k,
            self._policy.retrieval.final_top_k,
        )
        retriever = HybridRetriever(
            uow_factory=self._uow_factory,
            policy=self._policy,
            llm=self._llm,
            vector_service=(
                KBVectorService(
                    self._embeddings,
                    scope=self._owner_scope,
                    enabled=self._policy.vector_enabled,
                )
                if self._embeddings is not None
                else None
            ),
        )
        response = await retriever.retrieve(
            self._kb_id,
            self._version_id,
            query,
            limit=effective_limit,
        )
        if not response.items:
            return ToolResult(
                data="未找到相关文档片段",
                citations=[],
            )
        lines = []
        citations: list[KnowledgeCitation] = []
        for item in response.items:
            chunk = item.chunk
            presented = item.parent or chunk
            doc = item.document
            query_string = urlencode(
                {
                    "page": item.citation.page_no or "",
                    "chunk": item.citation.chunk_id,
                    "version": item.citation.version_id,
                    "revision": item.citation.document_revision_id,
                }
            )
            href = f"kbdoc://{doc.id}?{query_string}"
            title = f"《{doc.title}》p{presented.page_no or '?'}"
            if presented.heading_path:
                title += f"·{presented.heading_path}"
            lines.append(f"[score={item.score:.3f}] [{title}]({href})\n{item.content[:1200]}")
            citations.append(item.citation)
        return ToolResult(
            data="\n\n---\n\n".join(lines),
            citations=deduplicate_citations(citations),
        )

    @tool(
        name="graph_search",
        description="按实体名称搜索知识图谱关系",
        parameters={
            "entity": {"type": "string", "description": "实体名称"},
        },
        required=["entity"],
        policy=READ_SAFE,
    )
    async def graph_search(self, entity: str) -> ToolResult[str]:
        if not self._policy.graph_enabled:
            unavailable = "知识图谱检索已被本次执行策略禁用"
            return ToolResult(
                success=False,
                message=unavailable,
                data=unavailable,
            )
        async with self._uow_factory() as uow:
            version = await uow.knowledge_version.get_version(
                self._version_id,
                knowledge_base_id=self._kb_id,
            )
            if (
                version is None
                or version.id != self._version_id
                or version.knowledge_base_id != self._kb_id
                or version.published_at is None
                or version.state
                not in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
                or not bool(version.capabilities.get("graph_search", False))
            ):
                unavailable = "知识图谱检索不可用"
                return ToolResult(
                    success=False,
                    message=unavailable,
                    data=unavailable,
                )
            entities = await uow.knowledge_base.list_entities_for_version(
                self._kb_id,
                self._version_id,
                name=entity,
            )
            relations = await uow.knowledge_base.list_relations_for_entities_for_version(
                self._kb_id,
                self._version_id,
                [item.id for item in entities],
            )
            known_ids = {item.id for item in entities}
            endpoint_ids = {
                endpoint_id
                for relation in relations
                for endpoint_id in (
                    relation.src_entity_id,
                    relation.dst_entity_id,
                )
            }
            missing_ids = sorted(endpoint_ids - known_ids)
            if missing_ids:
                entities.extend(
                    await uow.knowledge_base.get_entities_by_ids_for_version(
                        self._kb_id,
                        self._version_id,
                        missing_ids,
                    )
                )
            evidence = await uow.knowledge_base.get_chunks_by_ids_for_version(
                self._kb_id,
                self._version_id,
                sorted({relation.chunk_id for relation in relations if relation.chunk_id}),
            )
        if not entities:
            return ToolResult(data=f"未找到实体: {entity}")
        entity_by_id = {item.id: item for item in entities}
        lines = [
            f"## 实体: {item.name} ({item.type})\n{item.description}" for item in entities[:10]
        ]
        if relations:
            lines.append("## 关系")
        for relation in relations[:30]:
            src = entity_by_id.get(relation.src_entity_id)
            dst = entity_by_id.get(relation.dst_entity_id)
            if src is None or dst is None:
                continue
            lines.append(f"- {src.name} --{relation.relation}--> {dst.name}")
        citations = [
            KnowledgeCitation(
                version_id=self._version_id,
                document_revision_id=record.document_revision_id,
                doc_id=record.document.id,
                page_no=record.chunk.page_no,
                chunk_id=record.chunk.id,
            )
            for record in evidence
            if (
                record.chunk.kb_id == self._kb_id
                and record.chunk.version_id == self._version_id
                and record.document.kb_id == self._kb_id
            )
        ]
        return ToolResult(
            data="\n".join(lines),
            citations=deduplicate_citations(citations),
        )

    @tool(
        name="get_document",
        description="读取指定文档的原文片段，可按页码过滤",
        parameters={
            "doc_id": {"type": "string", "description": "文档ID"},
            "page": {"type": "integer", "description": "页码，可选"},
            "cursor": {
                "type": "string",
                "description": "上一页返回的不透明游标，可选",
            },
            "limit": {
                "type": "integer",
                "description": "返回片段数，范围 1 到 200，默认 30",
                "minimum": 1,
                "maximum": 200,
            },
        },
        required=["doc_id"],
        policy=READ_SAFE,
    )
    async def get_document(
        self,
        doc_id: str,
        page: int | None = None,
        cursor: str | None = None,
        limit: int = 30,
    ) -> ToolResult[str]:
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise ValueError("page must be at least 1")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        async with self._uow_factory() as uow:
            resolved = await uow.knowledge_base.get_document_for_version(
                self._kb_id,
                self._version_id,
                doc_id,
            )
            if not resolved:
                return ToolResult(data=f"未找到文档: {doc_id}")
            doc, revision_id = resolved
            source_page = await uow.knowledge_base.read_document_page_for_version(
                self._kb_id,
                self._version_id,
                doc_id,
                revision_id,
                page_no=page,
                cursor=cursor,
                limit=limit,
            )
        if not source_page.items:
            return ToolResult(data=f"文档《{doc.title}》暂无可读取片段")
        lines = [f"# 《{doc.title}》"]
        citations: list[KnowledgeCitation] = []
        for chunk in source_page.items:
            lines.append(f"## p{chunk.page_no or '?'} {chunk.heading_path}\n{chunk.content}")
            citations.append(
                KnowledgeCitation(
                    version_id=self._version_id,
                    document_revision_id=revision_id,
                    doc_id=doc.id,
                    page_no=chunk.page_no,
                    chunk_id=chunk.id,
                )
            )
        if source_page.next_cursor is not None:
            lines.append(f"next_cursor: {source_page.next_cursor}")
        return ToolResult(
            data="\n\n".join(lines),
            citations=deduplicate_citations(citations),
        )
