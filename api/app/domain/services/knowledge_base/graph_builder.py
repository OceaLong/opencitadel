#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GraphRAG entity/relation extraction for knowledge bases."""
import asyncio
import logging
import uuid
from typing import Callable, List, Optional

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.knowledge_base import KnowledgeChunk, KnowledgeEntity, KnowledgeRelation
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

GRAPH_EXTRACT_PROMPT = """从以下企业文档片段中抽取对问答有帮助的实体与关系。
只返回 JSON，格式:
{
  "entities": [{"name": "...", "type": "组织|产品|流程|制度|人|地点|概念|其他", "description": "..."}],
  "relations": [{"src": "实体名", "dst": "实体名", "relation": "关系说明"}]
}

文档片段:
{content}
"""


class GraphBuilder:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: Optional[LLM],
            json_parser: JSONParser,
            *,
            max_parent_chunks_per_doc: int = 200,
            concurrency: int = 3,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._json_parser = json_parser
        self._max_parent_chunks_per_doc = max_parent_chunks_per_doc
        self._concurrency = max(1, concurrency)

    async def build(self, kb_id: str, parent_chunks: List[KnowledgeChunk]) -> tuple[int, int, Optional[str]]:
        if not self._llm:
            return 0, 0, "GraphRAG 跳过：LLM 不可用"
        from collections import defaultdict

        by_doc: dict[str, list[KnowledgeChunk]] = defaultdict(list)
        for chunk in parent_chunks:
            by_doc[chunk.doc_id].append(chunk)
        selected: list[KnowledgeChunk] = []
        skipped = 0
        for chunks in by_doc.values():
            capped = chunks[: self._max_parent_chunks_per_doc]
            selected.extend(capped)
            skipped += max(0, len(chunks) - len(capped))
        semaphore = asyncio.Semaphore(self._concurrency)
        entity_by_name: dict[str, KnowledgeEntity] = {}
        relations: list[KnowledgeRelation] = []

        async def extract(chunk: KnowledgeChunk):
            async with semaphore:
                try:
                    return chunk, await self._extract_chunk(chunk)
                except Exception as exc:
                    logger.warning("GraphRAG 片段抽取失败 chunk=%s: %s", chunk.id, exc)
                    return chunk, {}

        results = await asyncio.gather(*(extract(chunk) for chunk in selected))
        for chunk, payload in results:
            entities = payload.get("entities") if isinstance(payload, dict) else []
            rels = payload.get("relations") if isinstance(payload, dict) else []
            if not isinstance(entities, list):
                entities = []
            for item in entities:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                entity_by_name.setdefault(
                    key,
                    KnowledgeEntity(
                        id=str(uuid.uuid4()),
                        kb_id=kb_id,
                        name=name,
                        type=str(item.get("type") or ""),
                        description=str(item.get("description") or ""),
                    ),
                )
            if not isinstance(rels, list):
                continue
            for item in rels:
                if not isinstance(item, dict):
                    continue
                src_key = str(item.get("src") or "").strip().lower()
                dst_key = str(item.get("dst") or "").strip().lower()
                if not src_key or not dst_key or src_key not in entity_by_name or dst_key not in entity_by_name:
                    continue
                relations.append(
                    KnowledgeRelation(
                        id=str(uuid.uuid4()),
                        kb_id=kb_id,
                        src_entity_id=entity_by_name[src_key].id,
                        dst_entity_id=entity_by_name[dst_key].id,
                        relation=str(item.get("relation") or ""),
                        chunk_id=chunk.id,
                    )
                )

        async with self._uow_factory() as uow:
            await uow.knowledge_base.save_entities(list(entity_by_name.values()))
            await uow.knowledge_base.save_relations(relations)
        warning = f"图谱抽取已达上限，跳过 {skipped} 个父块" if skipped else None
        return len(entity_by_name), len(relations), warning

    async def _extract_chunk(self, chunk: KnowledgeChunk) -> dict:
        response = await self._llm.invoke(
            messages=[
                {
                    "role": "user",
                    "content": GRAPH_EXTRACT_PROMPT.format(content=chunk.content[:6000]),
                }
            ]
        )
        text = _extract_llm_text_content(response)
        parsed = await self._json_parser.invoke(text, default_value={})
        return parsed if isinstance(parsed, dict) else {}


def _extract_llm_text_content(response: dict) -> str:
    content = response.get("content") or ""
    if isinstance(content, str) and content.strip():
        return content
    reasoning = response.get("reasoning_content") or ""
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return "{}"
