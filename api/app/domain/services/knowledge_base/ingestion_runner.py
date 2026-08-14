#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Staged, PostgreSQL-authoritative knowledge-base ingestion."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import AsyncGenerator, Optional

from app.domain.errors import ConflictError
from app.domain.config_port import get_runtime_config
from app.application.services.resource_build_service import ResourceBuildService
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.models.error_codes import (
    DOCUMENT_PARSE_FAILED,
    EMBEDDING_UNAVAILABLE,
)
from app.domain.models.event import (
    BaseEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    StepEvent,
    StepEventStatus,
)
from app.domain.models.knowledge_base import (
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.plan import Step
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
    build_phase_regresses,
)
from app.domain.repositories.patch import UNSET, UnsetType
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.chunker import (
    ChunkSettings,
    KBChunker,
)
from app.domain.services.knowledge_base.graph_builder import (
    GraphBudget,
    GraphBuildResult,
    GraphBuilder,
)
from app.domain.services.knowledge_base.ocr_service import ocr_pdf_to_blocks
from app.domain.services.knowledge_base.parsers import (
    PageBlock,
    parse_document,
)
from app.domain.services.knowledge_base.web_connector import (
    fetch_confluence_document,
    fetch_feishu_document,
    fetch_web_document,
)

logger = logging.getLogger(__name__)

_TERMINAL_BUILD_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}


def _step_event(
    step_id: str,
    description: str,
    status: StepEventStatus,
) -> StepEvent:
    return StepEvent(
        step=Step(id=step_id, description=description),
        status=status,
    )


class KBIngestionRunner:
    """Build one immutable candidate and publish it with the Task 2 CAS."""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        file_storage: FileStorage,
        llm: Optional[LLM] = None,
        ocr_llm: Optional[LLM] = None,
        json_parser: Optional[JSONParser] = None,
        build_service: ResourceBuildService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._llm = llm
        self._ocr_llm = ocr_llm if ocr_llm is not None else llm
        self._json_parser = json_parser
        self._build_service = build_service

    async def cancel(self, build_id: str) -> None:
        """Persist a transport-requested cancellation exactly once."""
        if self._build_service is None:
            raise RuntimeError(
                "knowledge ingestion requires ResourceBuildService"
            )
        try:
            build, _version, _manifest, _kb, scope = (
                await self._load_candidate(build_id)
            )
        except Exception as exc:
            await self._fail_orphan_build(
                build_id,
                error=(
                    "knowledge-base build closure is incomplete during "
                    f"cancellation: {exc}"
                ),
            )
            return
        if build.state in _TERMINAL_BUILD_STATES:
            return
        await self._fail_build(
            build,
            scope=scope,
            error="knowledge-base build cancelled",
            error_code="BUILD_CANCELLED",
            state=BuildState.CANCELLED,
        )

    async def reconcile_stale(
        self,
        build_id: str,
        *,
        error: str = "knowledge-base build heartbeat expired",
    ) -> BuildState | None:
        """Authoritatively reconcile a stale build, including orphans."""
        if self._build_service is None:
            raise RuntimeError(
                "knowledge ingestion requires ResourceBuildService"
            )
        async with self._uow_factory() as uow:
            build = await uow.resource_governance.get_build(
                build_id,
                for_update=False,
            )
        if build is None:
            return None
        if build.resource_kind is not ResourceKind.KNOWLEDGE_BASE:
            return None
        if build.state in _TERMINAL_BUILD_STATES:
            return build.state
        return await self._fail_build(
            build,
            scope=OwnerScope.personal(build.created_by),
            error=error,
            error_code="BUILD_STALE",
        )

    async def run(self, build_id: str) -> AsyncGenerator[BaseEvent, None]:
        context: tuple[
            ResourceBuild,
            KnowledgeBaseVersion,
            list[KnowledgeVersionDocument],
            KnowledgeBase,
            OwnerScope,
        ] | None = None
        published_committed = False
        try:
            if self._build_service is None:
                raise RuntimeError(
                    "knowledge ingestion requires ResourceBuildService"
                )
            context = await self._load_candidate(build_id)
            build, version, manifest, kb, scope = context
            if build.state in _TERMINAL_BUILD_STATES:
                # Duplicate Redis delivery after durable completion is a no-op.
                return
            if (
                version.state
                in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
                and version.published_at is not None
                and kb.active_version_id == version.id
            ):
                await self._complete_published_build(
                    build,
                    version,
                    scope,
                )
                yield DoneEvent()
                return
            if (
                version.state is not KnowledgeVersionState.BUILDING
                or version.published_at is not None
            ):
                raise ConflictError(
                    "resource build candidate is not publishable"
                )

            progress_floor = build.progress
            kb_id = build.resource_id
            version_id = build.version_id
            await self._event(
                build,
                scope,
                phase="parse",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.01),
                payload={"phase": "parse"},
            )

            runtime = get_runtime_config()
            cfg = runtime.knowledge_base
            graph_resume_metrics = dict(build.metrics)
            graph_resume_cursor = graph_resume_metrics.get(
                "graph_cursor"
            )
            if not isinstance(graph_resume_cursor, str):
                graph_resume_cursor = None
            chunker = KBChunker(
                settings=ChunkSettings(
                    parent_max_chars=cfg.chunk.parent_max_chars,
                    child_max_chars=cfg.chunk.child_max_chars,
                    overlap=cfg.chunk.overlap,
                )
            )

            yield _step_event(
                "parse",
                "正在解析候选版本文档...",
                StepEventStatus.STARTED,
            )
            revisions, documents = await self._load_manifest_material(
                version,
                manifest,
            )
            parent_version = await self._load_parent_version(version)
            if not manifest:
                message = "知识库候选版本没有文档"
                await self._fail_build(
                    build,
                    scope=scope,
                    error=message,
                    error_code=DOCUMENT_PARSE_FAILED,
                )
                yield ErrorEvent(
                    error=message,
                    code=DOCUMENT_PARSE_FAILED,
                )
                return

            parsed: list[
                tuple[KnowledgeVersionDocument, list[PageBlock]]
            ] = []
            legacy_entries: list[KnowledgeVersionDocument] = []
            parse_errors: list[str] = []
            for entry in manifest:
                revision = revisions.get(entry.document_revision_id)
                document = documents.get(entry.document_id)
                if (
                    revision is None
                    or revision.document_id != entry.document_id
                    or document is None
                    or document.kb_id != kb_id
                ):
                    raise ValueError(
                        "candidate manifest document/revision closure "
                        "is incomplete"
                    )
                if (
                    entry.state is DocumentRevisionState.INDEXED
                    and revision.state is DocumentRevisionState.INDEXED
                    and revision.needs_chunk_clone
                    and not revision.parsed_blocks
                ):
                    # c7 marks immutable imported revisions that have indexed
                    # chunks but no durable parse blocks. Because the marker
                    # lives on the revision, reuse remains valid through v2,
                    # v3, and later manifests that retain that exact content.
                    legacy_entries.append(entry)
                    continue
                try:
                    if (
                        revision.state
                        in {
                            DocumentRevisionState.PARSED,
                            DocumentRevisionState.INDEXING,
                            DocumentRevisionState.INDEXED,
                        }
                        and revision.parsed_blocks
                    ):
                        blocks = _blocks_from_durable(revision)
                        page_count = revision.page_count
                        warning = revision.warning
                    else:
                        await self._transition(
                            version,
                            entry,
                            DocumentRevisionState.PARSING,
                            error=None,
                            warning=None,
                        )
                        blocks, page_count, warning = (
                            await self._parse_document(
                                _revision_document(document, revision)
                            )
                        )
                        if not blocks:
                            raise ValueError(
                                "文档未提取到可索引文本"
                            )
                        await self._transition(
                            version,
                            entry,
                            DocumentRevisionState.PARSED,
                            parsed_blocks=_durable_blocks(blocks),
                            page_count=page_count,
                            error=None,
                            warning=warning,
                        )
                    if not blocks:
                        raise ValueError("文档未提取到可索引文本")
                    parsed.append((entry, blocks))
                except Exception as exc:
                    logger.exception(
                        "候选文档解析失败 build=%s doc=%s: %s",
                        build_id,
                        entry.document_id,
                        exc,
                    )
                    parse_errors.append(str(exc))
                    await self._transition(
                        version,
                        entry,
                        DocumentRevisionState.FAILED,
                        error=str(exc),
                    )

            parsed_count = len(parsed) + len(legacy_entries)
            failed_document_count = len(manifest) - parsed_count
            await self._event(
                build,
                scope,
                phase="parse",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.20),
                payload={
                    "document_count": len(manifest),
                    "parsed_document_count": parsed_count,
                    "failed_document_count": failed_document_count,
                },
            )
            yield _step_event(
                "parse",
                f"文档解析完成: {parsed_count}/{len(manifest)}",
                StepEventStatus.COMPLETED,
            )
            if parsed_count == 0:
                detail = "；".join(parse_errors[:3])
                message = (
                    f"全部候选文档解析失败: {detail}"
                    if detail
                    else "全部候选文档解析失败"
                )
                await self._fail_build(
                    build,
                    scope=scope,
                    error=message,
                    error_code=DOCUMENT_PARSE_FAILED,
                )
                yield ErrorEvent(
                    error=message,
                    code=DOCUMENT_PARSE_FAILED,
                )
                return

            yield _step_event(
                "chunk",
                "正在为候选版本分块...",
                StepEventStatus.STARTED,
            )
            parents: list[KnowledgeChunk] = []
            children: list[KnowledgeChunk] = []
            vector_degraded = not chunker._vector.enabled
            if legacy_entries:
                if version.parent_version_id is None:
                    raise ValueError(
                        "legacy candidate clone requires a parent version"
                    )
                async with self._uow_factory() as uow:
                    cloned = (
                        await uow.knowledge_base.clone_version_chunks(
                            kb_id,
                            version.parent_version_id,
                            version_id,
                            [
                                entry.document_id
                                for entry in legacy_entries
                            ],
                        )
                    )
                parents.extend(
                    chunk
                    for chunk in cloned
                    if chunk.level.value == "parent"
                )
                children.extend(
                    chunk
                    for chunk in cloned
                    if chunk.level.value == "child"
                )
                cloned_child_docs = {
                    chunk.doc_id
                    for chunk in children
                    if chunk.doc_id
                    in {
                        entry.document_id
                        for entry in legacy_entries
                    }
                }
                if cloned_child_docs != {
                    entry.document_id
                    for entry in legacy_entries
                }:
                    raise ValueError(
                        "legacy candidate clone produced incomplete chunks"
                    )
                if any(not chunk.embedding for chunk in children):
                    vector_degraded = True
            for entry, blocks in parsed:
                try:
                    if entry.state is not DocumentRevisionState.INDEXED:
                        await self._transition(
                            version,
                            entry,
                            DocumentRevisionState.INDEXING,
                        )
                    doc_parents, doc_children = await chunker.build_chunks(
                        kb_id,
                        entry.document_id,
                        blocks,
                        version_id=version_id,
                    )
                    if not doc_children:
                        raise ValueError("未生成可索引子块")
                    parents.extend(doc_parents)
                    children.extend(doc_children)
                    if any(not chunk.embedding for chunk in doc_children):
                        vector_degraded = True
                    await self._transition(
                        version,
                        entry,
                        DocumentRevisionState.INDEXED,
                    )
                except Exception as exc:
                    await self._transition(
                        version,
                        entry,
                        DocumentRevisionState.FAILED,
                        error=f"分块失败: {exc}",
                    )
                    message = f"候选版本分块失败: {exc}"
                    await self._fail_build(
                        build,
                        scope=scope,
                        error=message,
                        error_code=DOCUMENT_PARSE_FAILED,
                    )
                    yield ErrorEvent(
                        error=message,
                        code=DOCUMENT_PARSE_FAILED,
                    )
                    return
            if not children:
                raise ValueError("候选版本未生成检索索引")
            await self._event(
                build,
                scope,
                phase="chunk",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.38),
                payload={
                    "parent_chunk_count": len(parents),
                    "child_chunk_count": len(children),
                },
            )
            yield _step_event(
                "chunk",
                f"分块完成: 父块 {len(parents)}，子块 {len(children)}",
                StepEventStatus.COMPLETED,
            )

            yield _step_event(
                "keyword_index",
                "正在写入候选关键词索引...",
                StepEventStatus.STARTED,
            )
            try:
                async with self._uow_factory() as uow:
                    if graph_resume_cursor is None:
                        await uow.knowledge_base.replace_candidate_chunks(
                            kb_id,
                            version_id,
                            [*parents, *children],
                        )
            except Exception as exc:
                message = f"关键词索引写入失败: {exc}"
                await self._fail_build(
                    build,
                    scope=scope,
                    error=message,
                    error_code=DOCUMENT_PARSE_FAILED,
                )
                yield ErrorEvent(
                    error=message,
                    code=DOCUMENT_PARSE_FAILED,
                )
                return
            await self._event(
                build,
                scope,
                phase="keyword_index",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.52),
                payload={"keyword_search": True},
            )
            yield _step_event(
                "keyword_index",
                f"关键词索引完成: {len(children)} 子块",
                StepEventStatus.COMPLETED,
            )

            vector_search = bool(children) and not vector_degraded
            await self._event(
                build,
                scope,
                phase="vector_index",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.64),
                payload={"vector_search": vector_search},
            )

            # A fresh candidate graph was cleared exactly once together with
            # candidate chunks above. A resumed graph keeps its checkpointed
            # rows and never lets a worker delete another batch.
            graph_search = False
            graph_failed = False
            graph_warning: str | None = None
            graph_result: GraphBuildResult | None = None
            if cfg.graphrag.enabled:
                yield _step_event(
                    "graph",
                    "正在构建候选知识图谱...",
                    StepEventStatus.STARTED,
                )
                if self._json_parser is None or self._llm is None:
                    graph_failed = True
                    graph_warning = "LLM 或 JSON 解析器不可用"
                else:
                    try:
                        deadline_value = graph_resume_metrics.get(
                            "graph_deadline_utc"
                        )
                        deadline_version = graph_resume_metrics.get(
                            "graph_deadline_version_id"
                        )
                        try:
                            durable_deadline = datetime.fromisoformat(
                                str(deadline_value)
                            )
                            if durable_deadline.tzinfo is None:
                                raise ValueError
                            durable_deadline = (
                                durable_deadline.astimezone(timezone.utc)
                            )
                            if deadline_version != version_id:
                                raise ValueError
                        except (TypeError, ValueError):
                            durable_deadline = (
                                datetime.now(timezone.utc)
                                + timedelta(
                                    seconds=(
                                        cfg.graphrag.deadline_seconds
                                    )
                                )
                            )
                        graph_resume_metrics.update(
                            {
                                "graph_deadline_utc": (
                                    durable_deadline.isoformat()
                                ),
                                "graph_deadline_version_id": version_id,
                            }
                        )
                        await self._event(
                            build,
                            scope,
                            phase="graph",
                            state=BuildState.RUNNING,
                            progress=max(progress_floor, 0.70),
                            payload={
                                "metrics": dict(
                                    graph_resume_metrics
                                )
                            },
                        )

                        async def checkpoint_graph(
                            graph_metrics: dict,
                        ) -> None:
                            merged_metrics = {
                                **graph_resume_metrics,
                                **graph_metrics,
                            }
                            for counter_name in (
                                "graph_processed_count",
                                "graph_llm_call_count",
                                "graph_token_count",
                                "graph_actual_token_count",
                                "graph_reserved_token_count",
                                "graph_admitted_chunk_count",
                                "graph_succeeded_count",
                                "graph_failed_count",
                                "graph_invalid_count",
                            ):
                                fallback_name = (
                                    "graph_token_count"
                                    if counter_name
                                    == "graph_actual_token_count"
                                    else counter_name
                                )
                                if (
                                    counter_name
                                    == "graph_reserved_token_count"
                                ):
                                    fallback_name = (
                                        "graph_token_count"
                                    )
                                merged_metrics[counter_name] = int(
                                    graph_resume_metrics.get(
                                        counter_name,
                                        graph_resume_metrics.get(
                                            fallback_name,
                                            0,
                                        ),
                                    )
                                ) + int(
                                    graph_metrics.get(counter_name, 0)
                                )
                            await self._event(
                                build,
                                scope,
                                phase="graph",
                                state=BuildState.RUNNING,
                                progress=max(progress_floor, 0.70),
                                payload={"metrics": merged_metrics},
                            )

                        graph_result = await GraphBuilder(
                                uow_factory=self._uow_factory,
                                llm=self._llm,
                                json_parser=self._json_parser,
                                max_parent_chunks_per_doc=(
                                    cfg.graphrag.max_parent_chunks_per_doc
                                ),
                                concurrency=cfg.graphrag.concurrency,
                            ).build(
                                kb_id,
                                parents,
                                version_id=version_id,
                                budget=GraphBudget(
                                    max_chunks=cfg.graphrag.max_chunks,
                                    max_llm_calls=(
                                        cfg.graphrag.max_llm_calls
                                    ),
                                    max_tokens=cfg.graphrag.max_tokens,
                                    deadline_seconds=(
                                        cfg.graphrag.deadline_seconds
                                    ),
                                ),
                                resume_cursor=graph_resume_cursor,
                                checkpoint=checkpoint_graph,
                                deadline_utc=(
                                    durable_deadline.isoformat()
                                ),
                                consumed_chunks=max(
                                    0,
                                    int(
                                        graph_resume_metrics.get(
                                            "graph_processed_count",
                                            0,
                                        )
                                    ),
                                ),
                                consumed_llm_calls=max(
                                    0,
                                    int(
                                        graph_resume_metrics.get(
                                            "graph_llm_call_count",
                                            0,
                                        )
                                    ),
                                ),
                                consumed_tokens=max(
                                    0,
                                    int(
                                        graph_resume_metrics.get(
                                            "graph_token_count",
                                            0,
                                        )
                                    ),
                                ),
                                consumed_actual_tokens=max(
                                    0,
                                    int(
                                        graph_resume_metrics.get(
                                            "graph_actual_token_count",
                                            graph_resume_metrics.get(
                                                "graph_token_count",
                                                0,
                                            ),
                                        )
                                    ),
                                ),
                                consumed_reserved_tokens=max(
                                    0,
                                    int(
                                        graph_resume_metrics.get(
                                            "graph_reserved_token_count",
                                            graph_resume_metrics.get(
                                                "graph_token_count",
                                                0,
                                            ),
                                        )
                                    ),
                                ),
                            )
                        entity_count = graph_result.entity_count
                        relation_count = graph_result.relation_count
                        graph_warning = graph_result.warning
                        graph_search = graph_result.complete
                        graph_failed = not graph_search
                        yield _step_event(
                            "graph",
                            (
                                "知识图谱完成: "
                                f"{entity_count} 实体, "
                                f"{relation_count} 关系"
                            ),
                            StepEventStatus.COMPLETED,
                        )
                    except Exception as exc:
                        logger.warning("候选 GraphRAG 降级: %s", exc)
                        graph_failed = True
                        graph_warning = str(exc)
            async with self._uow_factory() as uow:
                metrics = dict(
                    await uow.knowledge_base.get_candidate_index_metrics(
                        kb_id,
                        version_id,
                    )
                )
            if graph_result is not None and (
                int(metrics.get("entity_count", 0))
                != graph_result.entity_count
                or int(metrics.get("relation_count", 0))
                != graph_result.relation_count
            ):
                graph_search = False
                graph_failed = True
                graph_warning = "GRAPH_PERSISTENCE_COUNT_MISMATCH"
            await self._event(
                build,
                scope,
                phase="graph",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.76),
                payload={
                    "graph_search": graph_search,
                    "warning": graph_warning,
                    "health": (
                        {
                            "attempted": graph_result.attempted,
                            "succeeded": graph_result.succeeded,
                            "failed": graph_result.failed,
                            "invalid": graph_result.invalid,
                            "skipped": graph_result.skipped,
                            "persisted": graph_result.persisted,
                            "processed": graph_result.processed,
                            "calls": graph_result.calls,
                            "tokens": graph_result.tokens,
                            "reserved_tokens": (
                                graph_result.reserved_tokens
                            ),
                            "cursor": graph_result.cursor,
                            "degraded_reason": (
                                graph_result.degraded_reason
                            ),
                        }
                        if graph_result is not None
                        else None
                    ),
                },
            )
            committed_vector_count = int(
                metrics.get(
                    "vector_chunk_count",
                    len(children) if vector_search else 0,
                )
            )
            vector_search = (
                vector_search
                and committed_vector_count
                == int(metrics["child_chunk_count"])
            )
            if graph_result is not None:
                graph_attempted_total = int(
                    graph_resume_metrics.get(
                        "graph_attempted_count",
                        graph_resume_metrics.get(
                            "graph_llm_call_count",
                            0,
                        ),
                    )
                ) + graph_result.attempted
                graph_succeeded_total = int(
                    graph_resume_metrics.get(
                        "graph_succeeded_count",
                        0,
                    )
                ) + graph_result.succeeded
                graph_failed_total = int(
                    graph_resume_metrics.get(
                        "graph_failed_count",
                        0,
                    )
                ) + graph_result.failed
                graph_invalid_total = int(
                    graph_resume_metrics.get(
                        "graph_invalid_count",
                        0,
                    )
                ) + graph_result.invalid
                graph_processed_total = int(
                    graph_resume_metrics.get(
                        "graph_processed_count", 0
                    )
                ) + graph_result.processed
                graph_calls_total = int(
                    graph_resume_metrics.get(
                        "graph_llm_call_count", 0
                    )
                ) + graph_result.calls
                graph_tokens_total = int(
                    graph_resume_metrics.get("graph_token_count", 0)
                ) + graph_result.tokens
                graph_actual_tokens_total = int(
                    graph_resume_metrics.get(
                        "graph_actual_token_count",
                        graph_resume_metrics.get(
                            "graph_token_count",
                            0,
                        ),
                    )
                ) + graph_result.tokens
                graph_reserved_tokens_total = int(
                    graph_resume_metrics.get(
                        "graph_reserved_token_count",
                        graph_resume_metrics.get(
                            "graph_token_count",
                            0,
                        ),
                    )
                ) + graph_result.reserved_tokens
                metrics.update(
                    {
                        "graph_attempted_count": graph_attempted_total,
                        "graph_succeeded_count": graph_succeeded_total,
                        "graph_failed_count": graph_failed_total,
                        "graph_invalid_count": graph_invalid_total,
                        "graph_skipped_count": graph_result.skipped,
                        "graph_processed_count": graph_processed_total,
                        "graph_llm_call_count": graph_calls_total,
                        "graph_token_count": graph_tokens_total,
                        "graph_actual_token_count": (
                            graph_actual_tokens_total
                        ),
                        "graph_reserved_token_count": (
                            graph_reserved_tokens_total
                        ),
                        "graph_admitted_chunk_count": int(
                            graph_resume_metrics.get(
                                "graph_admitted_chunk_count",
                                graph_resume_metrics.get(
                                    "graph_llm_call_count",
                                    0,
                                ),
                            )
                        )
                        + graph_result.calls,
                        "graph_cursor": graph_result.cursor,
                        "graph_deadline_utc": (
                            graph_resume_metrics.get(
                                "graph_deadline_utc"
                            )
                        ),
                        "graph_deadline_version_id": version_id,
                        "graph_budget": {
                            "max_chunks": (
                                graph_result.budget.max_chunks
                                if graph_result.budget
                                else None
                            ),
                            "max_llm_calls": (
                                graph_result.budget.max_llm_calls
                                if graph_result.budget
                                else None
                            ),
                            "max_tokens": (
                                graph_result.budget.max_tokens
                                if graph_result.budget
                                else None
                            ),
                            "deadline_seconds": (
                                graph_result.budget.deadline_seconds
                                if graph_result.budget
                                else None
                            ),
                        },
                    }
                )
            if (
                metrics["indexed_document_count"] < 1
                or metrics["child_chunk_count"] < 1
                or (
                    metrics["indexed_document_count"]
                    + metrics["failed_document_count"]
                    != metrics["document_count"]
                )
            ):
                raise ValueError("候选版本索引闭包验证失败")
            await self._event(
                build,
                scope,
                phase="validate",
                state=BuildState.RUNNING,
                progress=max(progress_floor, 0.88),
                payload={"metrics": metrics},
            )

            degraded_reasons: list[str] = []
            if metrics["failed_document_count"]:
                degraded_reasons.append("DOCUMENT_PARTIAL")
            if not vector_search:
                degraded_reasons.append("EMBEDDING_UNAVAILABLE")
            if graph_failed:
                degraded_reasons.append(
                    (
                        graph_result.degraded_reason
                        if graph_result is not None
                        and graph_result.degraded_reason
                        else "GRAPH_UNAVAILABLE"
                    )
                )
            degraded_reasons = list(dict.fromkeys(degraded_reasons))
            capabilities = {
                "keyword_search": True,
                "vector_search": vector_search,
                "graph_search": graph_search,
            }
            version_state = (
                KnowledgeVersionState.DEGRADED
                if degraded_reasons
                else KnowledgeVersionState.READY
            )
            terminal_state = (
                BuildState.DEGRADED
                if degraded_reasons
                else BuildState.SUCCEEDED
            )

            # Candidate metadata and active pin commit in the same UoW. The
            # terminal build event is deliberately appended only afterwards.
            async with self._uow_factory() as uow:
                published = await uow.knowledge_version.publish_candidate(
                    version_id,
                    knowledge_base_id=kb_id,
                    expected_active_version_id=version.parent_version_id,
                    state=version_state,
                    capabilities=capabilities,
                    degraded_reasons=degraded_reasons,
                    metrics=metrics,
                )
                if not published:
                    raise ConflictError(
                        "knowledge-base candidate lost active-version CAS"
                    )
                current = await uow.knowledge_base.get_kb(kb_id)
                if current is None:
                    raise RuntimeError("published knowledge base disappeared")
                current.status = KBStatus.READY
                current.doc_count = metrics["document_count"]
                current.chunk_count = metrics["child_chunk_count"]
                current.vector_degraded = not vector_search
                if current.ingest_task_id == build.id:
                    current.ingest_task_id = None
                current.error = (
                    ", ".join(degraded_reasons)
                    if degraded_reasons
                    else None
                )
                current.updated_at = datetime.now()
                await uow.knowledge_base.save_kb(current)
            published_committed = True

            await self._event(
                build,
                scope,
                phase="publish",
                state=terminal_state,
                progress=1.0,
                payload={
                    "capabilities": capabilities,
                    "degraded_reasons": degraded_reasons,
                    "metrics": metrics,
                },
            )
            logger.info(
                "知识库候选发布完成 build=%s kb=%s version=%s state=%s",
                build.id,
                kb_id,
                version_id,
                terminal_state.value,
            )
            yield MessageEvent(
                role="assistant",
                message=(
                    f"文档知识库 **{kb.name}** 索引完成。\n\n"
                    f"- 本次处理文档: {len(manifest)}\n"
                    f"- 解析成功: {parsed_count}\n"
                    f"- 父块: {len(parents)}\n"
                    f"- 子块: {len(children)}"
                ),
            )
            yield DoneEvent()
        except asyncio.CancelledError:
            if context is not None:
                build, _version, _manifest, _kb, scope = context
                await self._fail_build(
                    build,
                    scope=scope,
                    error="knowledge-base build cancelled",
                    error_code="BUILD_CANCELLED",
                    state=BuildState.CANCELLED,
                )
            else:
                await self._fail_orphan_build(
                    build_id,
                    error=(
                        "knowledge-base build cancelled before closure "
                        "resolution"
                    ),
                    state=BuildState.CANCELLED,
                    error_code="BUILD_CANCELLED",
                )
            raise
        except Exception as exc:
            logger.exception("知识库候选摄取失败: %s", exc)
            reconciled_state: BuildState | None = None
            if context is not None and not published_committed:
                build, _version, _manifest, _kb, scope = context
                reconciled_state = await self._fail_build(
                    build,
                    scope=scope,
                    error=str(exc),
                    error_code=(
                        "PUBLISH_CONFLICT"
                        if isinstance(exc, ConflictError)
                        else "TASK_INFRA_FAILED"
                    ),
                )
            elif context is None:
                reconciled_state = await self._fail_orphan_build(
                    build_id,
                    error=(
                        "knowledge-base build closure is incomplete: "
                        f"{exc}"
                    ),
                )
            if reconciled_state in {
                BuildState.SUCCEEDED,
                BuildState.DEGRADED,
            }:
                yield DoneEvent()
                return
            code = (
                EMBEDDING_UNAVAILABLE
                if "embed" in str(exc).lower()
                else None
            )
            yield ErrorEvent(error=str(exc), code=code)

    async def _complete_published_build(
        self,
        build: ResourceBuild,
        version: KnowledgeBaseVersion,
        scope: OwnerScope,
    ) -> None:
        capabilities = {
            str(name): bool(value)
            for name, value in version.capabilities.items()
        }
        degraded_reasons = list(version.degraded_reasons)
        state = (
            BuildState.DEGRADED
            if version.state is KnowledgeVersionState.DEGRADED
            else BuildState.SUCCEEDED
        )
        await self._event(
            build,
            scope,
            phase="publish",
            state=state,
            progress=1.0,
            payload={
                "capabilities": capabilities,
                "degraded_reasons": degraded_reasons,
                "metrics": dict(version.metrics),
                "reconciled_after_publish": True,
            },
        )

    async def _load_candidate(
        self,
        build_id: str,
    ) -> tuple[
        ResourceBuild,
        KnowledgeBaseVersion,
        list[KnowledgeVersionDocument],
        KnowledgeBase,
        OwnerScope,
    ]:
        async with self._uow_factory() as uow:
            build = await uow.resource_governance.get_build(build_id)
            candidate = await uow.knowledge_version.get_build_candidate(
                build_id
            )
            if build is None or candidate is None:
                raise ValueError(
                    "knowledge-base build candidate not found"
                )
            version, manifest = candidate
            if (
                build.resource_kind is not ResourceKind.KNOWLEDGE_BASE
                or build.resource_id != version.knowledge_base_id
                or build.version_id != version.id
                or build.parent_version_id != version.parent_version_id
                or version.build_id != build.id
            ):
                raise ValueError(
                    "knowledge-base build/candidate identity mismatch"
                )
            kb = await uow.knowledge_base.get_kb(build.resource_id)
            if kb is None:
                raise ValueError("knowledge base not found")
        if kb.team_id:
            scope = OwnerScope.team(build.created_by, kb.team_id)
        else:
            scope = OwnerScope.personal(
                kb.owner_user_id or build.created_by
            )
        return build, version, manifest, kb, scope

    async def _load_manifest_material(
        self,
        version: KnowledgeBaseVersion,
        manifest: list[KnowledgeVersionDocument],
    ) -> tuple[
        dict[str, KnowledgeDocumentRevision],
        dict[str, KnowledgeDocument | None],
    ]:
        async with self._uow_factory() as uow:
            revisions = await uow.knowledge_version.get_revisions(
                [entry.document_revision_id for entry in manifest],
                knowledge_base_id=version.knowledge_base_id,
            )
            documents = {
                entry.document_id:
                await uow.knowledge_base.get_document_for_build(
                    entry.document_id
                )
                for entry in manifest
            }
        return revisions, documents

    async def _load_parent_version(
        self,
        version: KnowledgeBaseVersion,
    ) -> KnowledgeBaseVersion | None:
        if version.parent_version_id is None:
            return None
        async with self._uow_factory() as uow:
            return await uow.knowledge_version.get_version(
                version.parent_version_id,
                knowledge_base_id=version.knowledge_base_id,
            )

    async def _transition(
        self,
        version: KnowledgeBaseVersion,
        entry: KnowledgeVersionDocument,
        state: DocumentRevisionState,
        *,
        parsed_blocks: list[dict] | None | UnsetType = UNSET,
        page_count: int | None | UnsetType = UNSET,
        error: str | None | UnsetType = UNSET,
        warning: str | None | UnsetType = UNSET,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_version.transition_document(
                version.id,
                entry.document_id,
                knowledge_base_id=version.knowledge_base_id,
                state=state,
                parsed_blocks=parsed_blocks,
                page_count=page_count,
                error=error,
                warning=warning,
            )

    async def _event(
        self,
        build: ResourceBuild,
        scope: OwnerScope,
        *,
        phase: str,
        state: BuildState,
        progress: float,
        payload: dict,
    ) -> None:
        assert self._build_service is not None
        current = await self._build_service.require_build(
            build.id,
            scope,
        )
        if build_phase_regresses(
            ResourceKind.KNOWLEDGE_BASE,
            current.phase,
            phase,
        ):
            # Re-entry may replay idempotent candidate work internally, but its
            # durable event stream resumes at or after the persisted phase.
            return
        await self._build_service.append_event(
            build.id,
            phase=phase,
            state=state,
            progress=progress,
            payload=payload,
            scope=scope,
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id=build.resource_id,
            version_id=build.version_id,
        )

    async def _fail_build(
        self,
        build: ResourceBuild,
        *,
        scope: OwnerScope,
        error: str,
        error_code: str,
        state: BuildState = BuildState.FAILED,
    ) -> BuildState | None:
        persistence_error: Exception | None = None
        terminal_version: KnowledgeBaseVersion | None = None
        terminal_build: ResourceBuild | None = None
        failure_proven = False
        for attempt in range(3):
            try:
                async with self._uow_factory() as uow:
                    current_build = (
                        await uow.resource_governance.get_build(
                            build.id,
                            for_update=True,
                        )
                    )
                    if current_build is None:
                        return None
                    if current_build.state in _TERMINAL_BUILD_STATES:
                        return current_build.state
                    candidate = await uow.knowledge_version.get_version(
                        current_build.version_id,
                        knowledge_base_id=current_build.resource_id,
                    )
                    kb = await uow.knowledge_base.get_kb(
                        current_build.resource_id
                    )
                    terminal_build = current_build
                    if candidate is None or kb is None:
                        failure_proven = True
                        terminal_version = None
                        break
                    if (
                        candidate.state
                        in {
                            KnowledgeVersionState.READY,
                            KnowledgeVersionState.DEGRADED,
                        }
                        and candidate.published_at is not None
                        and kb.active_version_id == candidate.id
                    ):
                        terminal_version = candidate
                        failure_proven = False
                        break
                    failed = (
                        candidate.state is KnowledgeVersionState.FAILED
                    )
                    if not failed:
                        failed = (
                            await uow.knowledge_version.fail_candidate(
                                current_build.version_id,
                                knowledge_base_id=(
                                    current_build.resource_id
                                ),
                                metrics={"failed": 1},
                            )
                        )
                    if not failed:
                        # A publish/fail race changed the candidate after our
                        # read. Roll back and classify again in a fresh UoW.
                        raise RuntimeError(
                            "candidate terminal classification changed"
                        )
                    failure_proven = True
                    if (
                        kb is not None
                        and kb.active_version_id
                        != current_build.version_id
                    ):
                        kb.status = (
                            KBStatus.READY
                            if kb.active_version_id is not None
                            else KBStatus.FAILED
                        )
                        if kb.ingest_task_id == current_build.id:
                            kb.ingest_task_id = None
                        kb.error = error
                        kb.updated_at = datetime.now()
                        await uow.knowledge_base.save_kb(kb)
                persistence_error = None
                break
            except Exception as exc:
                persistence_error = exc
                logger.warning(
                    "候选失败状态持久化重试 build=%s attempt=%s: %s",
                    build.id,
                    attempt + 1,
                    exc,
                )
        if persistence_error is not None:
            # Keep the build non-terminal so stale reconciliation can repair
            # both candidate and event instead of recording a false terminal.
            raise RuntimeError(
                "failed to persist candidate failure"
            ) from persistence_error
        if terminal_version is not None:
            assert terminal_build is not None
            return await self._append_published_terminal_authoritative(
                terminal_build,
                terminal_version,
            )
        if not failure_proven or terminal_build is None:
            raise RuntimeError(
                "candidate failure was not authoritatively proven"
            )
        try:
            await self._append_authoritative_event(
                terminal_build,
                phase=(
                    "cancelled"
                    if state is BuildState.CANCELLED
                    else "failed"
                ),
                state=state,
                progress=terminal_build.progress,
                payload={
                    "error_code": error_code,
                    "error_message": error,
                },
            )
            return state
        except ConflictError:
            # A racing runner/reconciler committed the only terminal event.
            return None

    async def _fail_orphan_build(
        self,
        build_id: str,
        *,
        error: str,
        state: BuildState = BuildState.FAILED,
        error_code: str = "BUILD_CLOSURE_INVALID",
    ) -> BuildState | None:
        """Close an owner-orphaned shared build from PostgreSQL authority."""
        async with self._uow_factory() as uow:
            build = await uow.resource_governance.get_build(
                build_id,
                for_update=False,
            )
        if build is None:
            return None
        if build.resource_kind is not ResourceKind.KNOWLEDGE_BASE:
            return None
        if build.state in _TERMINAL_BUILD_STATES:
            return build.state
        return await self._fail_build(
            build,
            scope=OwnerScope.personal(build.created_by),
            error=error,
            error_code=error_code,
            state=state,
        )

    async def _append_published_terminal_authoritative(
        self,
        build: ResourceBuild,
        version: KnowledgeBaseVersion,
    ) -> BuildState:
        terminal_state = (
            BuildState.DEGRADED
            if version.state is KnowledgeVersionState.DEGRADED
            else BuildState.SUCCEEDED
        )
        await self._append_authoritative_event(
            build,
            phase="publish",
            state=terminal_state,
            progress=1.0,
            payload={
                "capabilities": {
                    str(name): bool(value)
                    for name, value in version.capabilities.items()
                },
                "degraded_reasons": list(version.degraded_reasons),
                "metrics": dict(version.metrics),
                "reconciled_after_publish": True,
            },
        )
        return terminal_state

    async def _append_authoritative_event(
        self,
        build: ResourceBuild,
        *,
        phase: str,
        state: BuildState,
        progress: float,
        payload: dict,
    ) -> None:
        assert self._build_service is not None
        await self._build_service.append_event_authoritative(
            build.id,
            phase=phase,
            state=state,
            progress=progress,
            payload=payload,
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id=build.resource_id,
            version_id=build.version_id,
        )

    async def _parse_document(
        self,
        doc: KnowledgeDocument,
    ) -> tuple[list[PageBlock], int, Optional[str]]:
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
        stream, file_info = await self._file_storage.download_file(
            doc.file_id
        )
        data = stream.read()
        if doc.source_type == KBSourceType.ZIP:
            return await self._parse_zip_document(
                data,
                file_info.filename,
            )
        result = await parse_document(
            data,
            file_info.mime_type,
            file_info.filename,
            max_bytes=cfg.document.max_bytes,
            max_pages=cfg.document.max_pages,
            ocr_mode=cfg.ocr.mode,
            ocr_max_pages=cfg.ocr.max_pages,
            expected_size=file_info.size,
        )
        blocks = result.blocks
        warning = result.warning
        if (
            cfg.ocr.mode != "off"
            and (
                file_info.mime_type == "application/pdf"
                or file_info.filename.lower().endswith(".pdf")
            )
            and (
                not blocks
                or sum(len(block.text or "") for block in blocks) < 32
            )
        ):
            ocr_blocks, ocr_warning = await ocr_pdf_to_blocks(
                data,
                self._ocr_llm,
                max_pages=cfg.ocr.max_pages,
            )
            if ocr_blocks:
                blocks = ocr_blocks
            if ocr_warning:
                warning = (
                    f"{warning}；{ocr_warning}"
                    if warning
                    else ocr_warning
                )
        if sum(len(block.text or "") for block in blocks) == 0:
            raise ValueError(warning or "文档未提取到可索引文本")
        return blocks, result.page_count, warning

    async def _parse_zip_document(
        self,
        data: bytes,
        filename: str,
    ) -> tuple[list[PageBlock], int, Optional[str]]:
        cfg = get_runtime_config().knowledge_base
        blocks: list[PageBlock] = []
        warnings: list[str] = []
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [
                name
                for name in archive.namelist()
                if not name.endswith("/")
            ]
            for name in names[: cfg.document.max_pages]:
                child_data = archive.read(name)
                mime = (
                    mimetypes.guess_type(name)[0]
                    or "application/octet-stream"
                )
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
                                heading_path=(
                                    f"{filename}/{name}/"
                                    f"{block.heading_path}"
                                ),
                                text=block.text,
                            )
                        )
                    if result.warning:
                        warnings.append(f"{name}: {result.warning}")
                except Exception as exc:
                    warnings.append(f"{name}: {exc}")
            if len(names) > cfg.document.max_pages:
                warnings.append(
                    f"压缩包共 {len(names)} 个文件，仅解析前 "
                    f"{cfg.document.max_pages} 个"
                )
        return (
            blocks,
            len(blocks),
            "；".join(warnings) if warnings else None,
        )


def _durable_blocks(blocks: list[PageBlock]) -> list[dict]:
    return [
        {
            "page_no": block.page_no,
            "heading_path": block.heading_path,
            "text": block.text,
        }
        for block in blocks
    ]


def _blocks_from_durable(
    revision: KnowledgeDocumentRevision,
) -> list[PageBlock]:
    blocks: list[PageBlock] = []
    for value in revision.parsed_blocks:
        if not isinstance(value, Mapping):
            raise ValueError("durable parsed block is not an object")
        blocks.append(
            PageBlock(
                page_no=int(value.get("page_no") or 1),
                heading_path=str(value.get("heading_path") or ""),
                text=str(value.get("text") or ""),
            )
        )
    return blocks


def _revision_document(
    document: KnowledgeDocument,
    revision: KnowledgeDocumentRevision,
) -> KnowledgeDocument:
    """Project immutable revision source material for the parser.

    Task 1 keeps the logical document immutable, so replacements must read
    their file/URL identity from the revision rather than the logical row.
    """
    source_ref = revision.source_ref.strip()
    file_id: str | None = None
    try:
        payload = json.loads(source_ref)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        candidate = payload.get("file_id")
        if isinstance(candidate, str) and candidate.strip():
            file_id = candidate.strip()
    if file_id:
        source_type = (
            KBSourceType.ZIP
            if document.source_type is KBSourceType.ZIP
            else KBSourceType.UPLOAD
        )
    else:
        source_type = (
            document.source_type
            if document.source_type
            in {
                KBSourceType.WEB,
                KBSourceType.CONFLUENCE,
                KBSourceType.FEISHU,
            }
            else KBSourceType.WEB
        )
    return document.model_copy(
        update={
            "source_type": source_type,
            "source_ref": source_ref,
            "file_id": file_id,
        },
        deep=True,
    )
