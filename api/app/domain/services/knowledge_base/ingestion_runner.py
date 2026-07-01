#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Parse, chunk, embed, and graph-index knowledge-base documents."""
import logging
import mimetypes
import zipfile
from io import BytesIO
from datetime import datetime
from typing import AsyncGenerator, Callable, Optional

from app.application.services.config_provider import get_runtime_config
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED, EMBEDDING_UNAVAILABLE
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, MessageEvent, StepEvent, StepEventStatus
from app.domain.models.knowledge_base import DocStatus, KBSourceType, KBStatus, KnowledgeChunk
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.chunker import ChunkSettings, KBChunker
from app.domain.services.knowledge_base.graph_builder import GraphBuilder
from app.domain.services.knowledge_base.ocr_service import ocr_pdf_to_blocks
from app.domain.services.knowledge_base.parsers import PageBlock, parse_document
from app.domain.services.knowledge_base.web_connector import (
    fetch_confluence_document,
    fetch_feishu_document,
    fetch_web_document,
)

logger = logging.getLogger(__name__)


class KBIngestionRunner:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
            llm: Optional[LLM] = None,
            json_parser: Optional[JSONParser] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._llm = llm
        self._json_parser = json_parser

    async def run(self, kb_id: str) -> AsyncGenerator[BaseEvent, None]:
        try:
            async with self._uow_factory() as uow:
                kb = await uow.knowledge_base.get_kb(kb_id)
            if not kb:
                yield ErrorEvent(error=f"知识库不存在: {kb_id}", code="TASK_INFRA_FAILED")
                return

            runtime = get_runtime_config()
            cfg = runtime.knowledge_base
            chunker = KBChunker(settings=ChunkSettings(
                parent_max_chars=cfg.chunk.parent_max_chars,
                child_max_chars=cfg.chunk.child_max_chars,
                overlap=cfg.chunk.overlap,
            ))

            yield StepEvent(status=StepEventStatus.STARTED, name="parse", description="正在解析文档...")
            await self._set_status(kb_id, KBStatus.PARSING)
            async with self._uow_factory() as uow:
                documents = await uow.knowledge_base.list_documents(kb_id)
            if not documents:
                await self._set_status(kb_id, KBStatus.FAILED, "知识库没有待解析文档")
                yield ErrorEvent(error="知识库没有待解析文档", code=DOCUMENT_PARSE_FAILED)
                return

            parsed: list[tuple[str, list[PageBlock]]] = []
            for doc in documents:
                try:
                    await self._update_document(doc.id, DocStatus.PARSING)
                    blocks, page_count, warning = await self._parse_document(doc)
                    parsed.append((doc.id, blocks))
                    await self._update_document(doc.id, DocStatus.READY, warning=warning, page_count=page_count)
                except Exception as exc:
                    logger.exception("文档解析失败 doc=%s: %s", doc.id, exc)
                    await self._update_document(doc.id, DocStatus.FAILED, error=str(exc))
            parsed_count = len(parsed)
            yield StepEvent(
                status=StepEventStatus.COMPLETED,
                name="parse",
                description=f"文档解析完成: {parsed_count}/{len(documents)}",
            )
            if not parsed:
                await self._set_status(kb_id, KBStatus.FAILED, "全部文档解析失败")
                yield ErrorEvent(error="全部文档解析失败", code=DOCUMENT_PARSE_FAILED)
                return

            yield StepEvent(status=StepEventStatus.STARTED, name="chunk", description="正在父子分块...")
            await self._set_status(kb_id, KBStatus.CHUNKING)

            all_parents: list[KnowledgeChunk] = []
            all_children: list[KnowledgeChunk] = []
            vector_degraded = False
            chunk_failed_docs: list[str] = []
            for doc_id, blocks in parsed:
                try:
                    parents, children = await chunker.build_chunks(kb_id, doc_id, blocks)
                    if not children:
                        raise ValueError("未生成可索引子块")
                    all_parents.extend(parents)
                    all_children.extend(children)
                except Exception as exc:
                    logger.warning("文档分块/向量化失败 doc=%s: %s", doc_id, exc)
                    chunk_failed_docs.append(doc_id)
                    vector_degraded = True
                    await self._update_document(doc_id, DocStatus.FAILED, error=f"分块/向量化失败: {exc}")
            if all_children and all(not chunk.embedding for chunk in all_children):
                vector_degraded = True
            if not all_children:
                await self._set_status(kb_id, KBStatus.FAILED, "全部文档分块失败，未生成检索索引")
                yield ErrorEvent(error="全部文档分块失败", code=DOCUMENT_PARSE_FAILED)
                return
            yield StepEvent(
                status=StepEventStatus.COMPLETED,
                name="chunk",
                description=f"分块完成: 父块 {len(all_parents)}，子块 {len(all_children)}",
            )

            yield StepEvent(status=StepEventStatus.STARTED, name="index", description="正在写入检索索引...")
            await self._set_status(kb_id, KBStatus.INDEXING)
            try:
                async with self._uow_factory() as uow:
                    await uow.knowledge_base.replace_index_chunks(kb_id, [*all_parents, *all_children])
            except Exception as exc:
                logger.exception("索引写入失败 kb=%s: %s", kb_id, exc)
                await self._set_status(kb_id, KBStatus.FAILED, f"索引写入失败: {exc}")
                yield ErrorEvent(error=f"索引写入失败: {exc}", code=EMBEDDING_UNAVAILABLE)
                return
            index_desc = f"索引完成: {len(all_children)} 子块"
            if vector_degraded:
                index_desc += "（语义向量不可用，已降级为 BM25）"
            yield StepEvent(status=StepEventStatus.COMPLETED, name="index", description=index_desc)

            graph_warning = None
            if cfg.graphrag.enabled:
                yield StepEvent(status=StepEventStatus.STARTED, name="graph", description="正在构建知识图谱...")
                await self._set_status(kb_id, KBStatus.GRAPH_BUILDING)
                if self._json_parser:
                    try:
                        entity_count, relation_count, graph_warning = await GraphBuilder(
                            uow_factory=self._uow_factory,
                            llm=self._llm,
                            json_parser=self._json_parser,
                            max_parent_chunks_per_doc=cfg.graphrag.max_parent_chunks_per_doc,
                            concurrency=cfg.graphrag.concurrency,
                        ).build(kb_id, all_parents)
                        graph_desc = f"知识图谱完成: {entity_count} 实体, {relation_count} 关系"
                        if graph_warning:
                            graph_desc += f"（{graph_warning}）"
                    except Exception as exc:
                        logger.warning("GraphRAG 降级: %s", exc)
                        graph_desc = f"知识图谱构建失败，已跳过: {exc}"
                else:
                    graph_desc = "知识图谱跳过：JSON 解析器不可用"
                yield StepEvent(status=StepEventStatus.COMPLETED, name="graph", description=graph_desc)

            failed_doc_count = len(documents) - parsed_count + len(chunk_failed_docs)
            kb_error = None
            if failed_doc_count > 0:
                kb_error = f"{failed_doc_count} 个文档解析或索引失败"
            async with self._uow_factory() as uow:
                kb = await uow.knowledge_base.get_kb(kb_id)
                if kb:
                    kb.status = KBStatus.READY if parsed_count > len(chunk_failed_docs) else KBStatus.FAILED
                    kb.error = kb_error
                    kb.doc_count = len(documents)
                    kb.chunk_count = len(all_children)
                    kb.vector_degraded = vector_degraded
                    kb.updated_at = datetime.now()
                    await uow.knowledge_base.save_kb(kb)

            logger.info(
                "知识库索引完成 kb=%s docs=%s parsed=%s chunks=%s vector_degraded=%s failed=%s",
                kb_id,
                len(documents),
                parsed_count,
                len(all_children),
                vector_degraded,
                failed_doc_count,
            )

            yield MessageEvent(
                role="assistant",
                message=(
                    f"文档知识库 **{kb.name if kb else kb_id}** 索引完成。\n\n"
                    f"- 文档: {len(documents)}\n"
                    f"- 解析成功: {parsed_count}\n"
                    f"- 父块: {len(all_parents)}\n"
                    f"- 子块: {len(all_children)}"
                ),
            )
            yield DoneEvent()
        except Exception as exc:
            logger.exception("知识库摄取失败: %s", exc)
            await self._set_status(kb_id, KBStatus.FAILED, str(exc))
            code = EMBEDDING_UNAVAILABLE if "embed" in str(exc).lower() else None
            yield ErrorEvent(error=str(exc), code=code)

    async def _parse_document(self, doc) -> tuple[list[PageBlock], int, Optional[str]]:
        runtime = get_runtime_config()
        cfg = runtime.knowledge_base
        if doc.source_type == KBSourceType.WEB:
            web_doc = await fetch_web_document(doc.source_ref)
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=cfg.document.max_bytes,
                max_pages=cfg.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if doc.source_type == KBSourceType.CONFLUENCE:
            web_doc = await fetch_confluence_document(doc.source_ref)
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=cfg.document.max_bytes,
                max_pages=cfg.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if doc.source_type == KBSourceType.FEISHU:
            web_doc = await fetch_feishu_document(doc.source_ref)
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=cfg.document.max_bytes,
                max_pages=cfg.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if not doc.file_id:
            raise ValueError("上传文档缺少 file_id")
        stream, file_info = await self._file_storage.download_file(doc.file_id)
        data = stream.read()
        if doc.source_type == KBSourceType.ZIP:
            return await self._parse_zip_document(data, file_info.filename)
        result = await parse_document(
            data,
            file_info.mime_type,
            file_info.filename,
            max_bytes=cfg.document.max_bytes,
            max_pages=cfg.document.max_pages,
            ocr_mode=cfg.ocr.mode,
            ocr_max_pages=cfg.ocr.max_pages,
        )
        blocks = result.blocks
        warning = result.warning
        if (
            cfg.ocr.mode != "off"
            and (file_info.mime_type == "application/pdf" or file_info.filename.lower().endswith(".pdf"))
            and (not blocks or sum(len(b.text or "") for b in blocks) < 32)
        ):
            ocr_blocks, ocr_warning = await ocr_pdf_to_blocks(
                data,
                self._llm,
                max_pages=cfg.ocr.max_pages,
            )
            if ocr_blocks:
                blocks = ocr_blocks
            if ocr_warning:
                warning = f"{warning}；{ocr_warning}" if warning else ocr_warning
        return blocks, result.page_count, warning

    async def _parse_zip_document(self, data: bytes, filename: str) -> tuple[list[PageBlock], int, Optional[str]]:
        runtime = get_runtime_config()
        cfg = runtime.knowledge_base
        blocks: list[PageBlock] = []
        warnings: list[str] = []
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            for idx, name in enumerate(names[: cfg.document.max_pages], start=1):
                child_data = archive.read(name)
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                try:
                    result = await parse_document(
                        child_data,
                        mime,
                        name,
                        max_bytes=cfg.document.max_bytes,
                        max_pages=cfg.document.max_pages,
                        ocr_mode=cfg.ocr.mode,
                        ocr_max_pages=cfg.ocr.max_pages,
                    )
                    for block in result.blocks:
                        blocks.append(
                            PageBlock(
                                page_no=len(blocks) + 1,
                                heading_path=f"{filename}/{name}/{block.heading_path}",
                                text=block.text,
                            )
                        )
                    if result.warning:
                        warnings.append(f"{name}: {result.warning}")
                except Exception as exc:
                    warnings.append(f"{name}: {exc}")
            if len(names) > cfg.document.max_pages:
                warnings.append(f"压缩包共 {len(names)} 个文件，仅解析前 {cfg.document.max_pages} 个")
        return blocks, len(blocks), "；".join(warnings) if warnings else None

    async def _set_status(self, kb_id: str, status: KBStatus, error: Optional[str] = None) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_base.update_status(kb_id, status, error)

    async def _update_document(
            self,
            doc_id: str,
            status: DocStatus,
            error: Optional[str] = None,
            warning: Optional[str] = None,
            page_count: Optional[int] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_base.update_document_status(doc_id, status, error, warning, page_count)
