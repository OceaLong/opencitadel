"""Staged, PostgreSQL-authoritative knowledge-base ingestion."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import zipfile
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO

from app.domain.errors import ConflictError
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.web_document import WebDocumentGateway
from app.domain.models.build_progress import (
    BuildProgress,
    BuildProgressStatus,
    build_done,
    build_error,
    build_message,
    build_step,
)
from app.domain.models.error_codes import (
    DOCUMENT_PARSE_FAILED,
    EMBEDDING_UNAVAILABLE,
)
from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
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
from app.domain.models.scope import OwnerScope
from app.domain.repositories.patch import UNSET, UnsetType
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import KnowledgeBaseExecutionPolicy
from app.domain.services.knowledge_base.chunker import (
    KBChunker,
)
from app.domain.services.knowledge_base.graph_builder import (
    GraphBudget,
    GraphBuilder,
    GraphBuildResult,
)
from app.domain.services.knowledge_base.ocr_service import ocr_pdf_to_blocks
from app.domain.services.knowledge_base.parsers import (
    PageBlock,
    parse_document,
)
from app.domain.services.knowledge_base.vector_service import KBVectorService
from app.domain.vector_port import EmbeddingPort

logger = logging.getLogger(__name__)


def _resource_scope(owner_user_id: str | None, team_id: str | None) -> OwnerScope | None:
    if team_id:
        return OwnerScope.team(owner_user_id or "resource-build", team_id)
    if owner_user_id:
        return OwnerScope.personal(owner_user_id)
    return None


def _step_event(
    step_id: str,
    description: str,
    status: BuildProgressStatus,
) -> BuildProgress:
    return build_step(step_id, description, status)


class KBIngestionRunner:
    """Build one immutable candidate and publish it with an ownership CAS."""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        file_storage: FileStorage,
        web_documents: WebDocumentGateway,
        llm: LLM | None = None,
        ocr_llm: LLM | None = None,
        json_parser: JSONParser | None = None,
        embeddings: EmbeddingPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._web_documents = web_documents
        self._llm = llm
        self._ocr_llm = ocr_llm if ocr_llm is not None else llm
        self._json_parser = json_parser
        self._embeddings = embeddings

    async def cancel(self, build_id: str) -> None:
        """Mark the unpublished artifact candidate failed after Run cancellation."""
        await self._fail_candidate(build_id, "knowledge-base build cancelled")

    async def run_build(
        self,
        build_id: str,
        *,
        policy: KnowledgeBaseExecutionPolicy,
        embedding_model_id: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> AsyncGenerator[BuildProgress, None]:
        context: (
            tuple[
                KnowledgeBaseVersion,
                list[KnowledgeVersionDocument],
                KnowledgeBase,
            ]
            | None
        ) = None
        published_committed = False
        try:
            context = await self._load_candidate(build_id)
            version, manifest, kb = context
            if (
                version.state
                in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
                and version.published_at is not None
                and kb.active_version_id == version.id
            ):
                yield build_done()
                return
            if (
                version.state is not KnowledgeVersionState.BUILDING
                or version.published_at is not None
            ):
                raise ConflictError("resource build candidate is not publishable")

            kb_id = version.knowledge_base_id
            version_id = version.id

            if embedding_model_id is not None and (
                embedding_dimensions != PLATFORM_EMBEDDING_DIMENSIONS
            ):
                raise ValueError("resource build embedding dimensions are invalid")
            if (
                policy.vector_enabled
                and self._embeddings is not None
                and embedding_model_id is None
            ):
                raise ConflictError("resource build is missing its frozen embedding model")
            graph_resume_metrics = dict(version.metrics)
            graph_resume_cursor = graph_resume_metrics.get("graph_cursor")
            if not isinstance(graph_resume_cursor, str):
                graph_resume_cursor = None
            vector_service = (
                KBVectorService(
                    self._embeddings,
                    scope=_resource_scope(kb.owner_user_id, kb.team_id),
                    enabled=policy.vector_enabled,
                    model_id=embedding_model_id,
                )
                if self._embeddings is not None
                else None
            )
            chunker = KBChunker(
                policy=policy.chunk,
                vector_service=vector_service,
            )

            yield _step_event(
                "parse",
                "正在解析候选版本文档...",
                BuildProgressStatus.STARTED,
            )
            revisions, documents = await self._load_manifest_material(
                version,
                manifest,
            )
            if not manifest:
                message = "知识库候选版本没有文档"
                await self._fail_candidate(build_id, message)
                yield build_error(
                    message=message,
                    failure_code=DOCUMENT_PARSE_FAILED,
                )
                return

            parsed: list[tuple[KnowledgeVersionDocument, list[PageBlock]]] = []
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
                    raise ValueError("candidate manifest document/revision closure is incomplete")
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
                        blocks, page_count, warning = await self._parse_document(
                            _revision_document(document, revision),
                            policy=policy,
                        )
                        if not blocks:
                            raise ValueError("文档未提取到可索引文本")
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
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.exception(
                        "候选文档解析失败 build=%s doc=%s",
                        build_id,
                        entry.document_id,
                    )
                    parse_errors.append(str(exc))
                    await self._transition(
                        version,
                        entry,
                        DocumentRevisionState.FAILED,
                        error=str(exc),
                    )

            parsed_count = len(parsed)
            len(manifest) - parsed_count
            yield _step_event(
                "parse",
                f"文档解析完成: {parsed_count}/{len(manifest)}",
                BuildProgressStatus.COMPLETED,
            )
            if parsed_count == 0:
                detail = "；".join(parse_errors[:3])
                message = f"全部候选文档解析失败: {detail}" if detail else "全部候选文档解析失败"
                await self._fail_candidate(build_id, message)
                yield build_error(
                    message=message,
                    failure_code=DOCUMENT_PARSE_FAILED,
                )
                return

            yield _step_event(
                "chunk",
                "正在为候选版本分块...",
                BuildProgressStatus.STARTED,
            )
            parents: list[KnowledgeChunk] = []
            children: list[KnowledgeChunk] = []
            vector_degraded = chunker._vector is None or not chunker._vector.enabled
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
                except (OSError, RuntimeError, ValueError) as exc:
                    await self._transition(
                        version,
                        entry,
                        DocumentRevisionState.FAILED,
                        error=f"分块失败: {exc}",
                    )
                    message = f"候选版本分块失败: {exc}"
                    await self._fail_candidate(build_id, message)
                    yield build_error(
                        message=message,
                        failure_code=DOCUMENT_PARSE_FAILED,
                    )
                    return
            if not children:
                raise ValueError("候选版本未生成检索索引")
            yield _step_event(
                "chunk",
                f"分块完成: 父块 {len(parents)}，子块 {len(children)}",
                BuildProgressStatus.COMPLETED,
            )

            yield _step_event(
                "keyword_index",
                "正在写入候选关键词索引...",
                BuildProgressStatus.STARTED,
            )
            try:
                async with self._uow_factory() as uow:
                    if graph_resume_cursor is None:
                        await uow.knowledge_base.replace_candidate_chunks(
                            kb_id,
                            version_id,
                            [*parents, *children],
                        )
                    await uow.commit()
            except (OSError, RuntimeError, ValueError) as exc:
                message = f"关键词索引写入失败: {exc}"
                await self._fail_candidate(build_id, message)
                yield build_error(
                    message=message,
                    failure_code=DOCUMENT_PARSE_FAILED,
                )
                return
            yield _step_event(
                "keyword_index",
                f"关键词索引完成: {len(children)} 子块",
                BuildProgressStatus.COMPLETED,
            )

            vector_search = bool(children) and not vector_degraded

            # A fresh candidate graph was cleared exactly once together with
            # candidate chunks above. A resumed graph keeps its checkpointed
            # rows and never lets a worker delete another batch.
            graph_search = False
            graph_failed = False
            graph_result: GraphBuildResult | None = None
            if policy.graphrag.enabled:
                yield _step_event(
                    "graph",
                    "正在构建候选知识图谱...",
                    BuildProgressStatus.STARTED,
                )
                if self._json_parser is None or self._llm is None:
                    graph_failed = True
                else:
                    try:
                        deadline_value = graph_resume_metrics.get("graph_deadline_utc")
                        deadline_version = graph_resume_metrics.get("graph_deadline_version_id")
                        try:
                            durable_deadline = datetime.fromisoformat(str(deadline_value))
                            if durable_deadline.tzinfo is None:
                                raise ValueError
                            durable_deadline = durable_deadline.astimezone(UTC)
                            if deadline_version != version_id:
                                raise ValueError
                        except (TypeError, ValueError):
                            durable_deadline = datetime.now(UTC) + timedelta(
                                seconds=policy.graphrag.deadline_seconds
                            )
                        graph_resume_metrics.update(
                            {
                                "graph_deadline_utc": (durable_deadline.isoformat()),
                                "graph_deadline_version_id": version_id,
                            }
                        )
                        await self._checkpoint_candidate(
                            version,
                            dict(graph_resume_metrics),
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
                                    if counter_name == "graph_actual_token_count"
                                    else counter_name
                                )
                                if counter_name == "graph_reserved_token_count":
                                    fallback_name = "graph_token_count"
                                merged_metrics[counter_name] = int(
                                    graph_resume_metrics.get(
                                        counter_name,
                                        graph_resume_metrics.get(
                                            fallback_name,
                                            0,
                                        ),
                                    )
                                ) + int(graph_metrics.get(counter_name, 0))
                            await self._checkpoint_candidate(
                                version,
                                merged_metrics,
                            )

                        graph_result = await GraphBuilder(
                            uow_factory=self._uow_factory,
                            llm=self._llm,
                            json_parser=self._json_parser,
                            max_parent_chunks_per_doc=(policy.graphrag.max_parent_chunks_per_doc),
                            concurrency=policy.graphrag.concurrency,
                        ).build(
                            kb_id,
                            parents,
                            version_id=version_id,
                            budget=GraphBudget(
                                max_chunks=policy.graphrag.max_chunks,
                                max_llm_calls=policy.graphrag.max_llm_calls,
                                max_tokens=policy.graphrag.max_tokens,
                                deadline_seconds=policy.graphrag.deadline_seconds,
                            ),
                            resume_cursor=graph_resume_cursor,
                            checkpoint=checkpoint_graph,
                            deadline_utc=(durable_deadline.isoformat()),
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
                        graph_search = graph_result.complete
                        graph_failed = not graph_search
                        yield _step_event(
                            "graph",
                            (f"知识图谱完成: {entity_count} 实体, {relation_count} 关系"),
                            BuildProgressStatus.COMPLETED,
                        )
                    except (OSError, RuntimeError, ValueError) as exc:
                        logger.warning("候选 GraphRAG 降级: %s", exc)
                        graph_failed = True
                        str(exc)
            async with self._uow_factory() as uow:
                metrics = dict(
                    await uow.knowledge_base.get_candidate_index_metrics(
                        kb_id,
                        version_id,
                    )
                )
            if graph_result is not None and (
                int(metrics.get("entity_count", 0)) != graph_result.entity_count
                or int(metrics.get("relation_count", 0)) != graph_result.relation_count
            ):
                graph_search = False
                graph_failed = True
            committed_vector_count = int(
                metrics.get(
                    "vector_chunk_count",
                    len(children) if vector_search else 0,
                )
            )
            vector_search = vector_search and committed_vector_count == int(
                metrics["child_chunk_count"]
            )
            if graph_result is not None:
                graph_attempted_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_attempted_count",
                            graph_resume_metrics.get(
                                "graph_llm_call_count",
                                0,
                            ),
                        )
                    )
                    + graph_result.attempted
                )
                graph_succeeded_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_succeeded_count",
                            0,
                        )
                    )
                    + graph_result.succeeded
                )
                graph_failed_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_failed_count",
                            0,
                        )
                    )
                    + graph_result.failed
                )
                graph_invalid_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_invalid_count",
                            0,
                        )
                    )
                    + graph_result.invalid
                )
                graph_processed_total = (
                    int(graph_resume_metrics.get("graph_processed_count", 0))
                    + graph_result.processed
                )
                graph_calls_total = (
                    int(graph_resume_metrics.get("graph_llm_call_count", 0)) + graph_result.calls
                )
                graph_tokens_total = (
                    int(graph_resume_metrics.get("graph_token_count", 0)) + graph_result.tokens
                )
                graph_actual_tokens_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_actual_token_count",
                            graph_resume_metrics.get(
                                "graph_token_count",
                                0,
                            ),
                        )
                    )
                    + graph_result.tokens
                )
                graph_reserved_tokens_total = (
                    int(
                        graph_resume_metrics.get(
                            "graph_reserved_token_count",
                            graph_resume_metrics.get(
                                "graph_token_count",
                                0,
                            ),
                        )
                    )
                    + graph_result.reserved_tokens
                )
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
                        "graph_actual_token_count": (graph_actual_tokens_total),
                        "graph_reserved_token_count": (graph_reserved_tokens_total),
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
                        "graph_deadline_utc": (graph_resume_metrics.get("graph_deadline_utc")),
                        "graph_deadline_version_id": version_id,
                        "graph_budget": {
                            "max_chunks": (
                                graph_result.budget.max_chunks if graph_result.budget else None
                            ),
                            "max_llm_calls": (
                                graph_result.budget.max_llm_calls if graph_result.budget else None
                            ),
                            "max_tokens": (
                                graph_result.budget.max_tokens if graph_result.budget else None
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
                    metrics["indexed_document_count"] + metrics["failed_document_count"]
                    != metrics["document_count"]
                )
            ):
                raise ValueError("候选版本索引闭包验证失败")

            degraded_reasons: list[str] = []
            if metrics["failed_document_count"]:
                degraded_reasons.append("DOCUMENT_PARTIAL")
            if not vector_search:
                degraded_reasons.append("EMBEDDING_UNAVAILABLE")
            if graph_failed:
                degraded_reasons.append(
                    graph_result.degraded_reason
                    if graph_result is not None and graph_result.degraded_reason
                    else "GRAPH_UNAVAILABLE"
                )
            degraded_reasons = list(dict.fromkeys(degraded_reasons))
            capabilities = {
                "keyword_search": True,
                "vector_search": vector_search,
                "graph_search": graph_search,
            }
            version_state = (
                KnowledgeVersionState.DEGRADED if degraded_reasons else KnowledgeVersionState.READY
            )
            # Candidate metadata and active pin commit in the same UoW.
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
                    raise ConflictError("knowledge-base candidate lost active-version CAS")
                current = await uow.knowledge_base.get_kb(kb_id)
                if current is None:
                    raise RuntimeError("published knowledge base disappeared")
                current.status = KBStatus.READY
                current.doc_count = metrics["document_count"]
                current.chunk_count = metrics["child_chunk_count"]
                current.vector_degraded = not vector_search
                current.error = ", ".join(degraded_reasons) if degraded_reasons else None
                current.updated_at = datetime.now(UTC)
                await uow.knowledge_base.save_kb(current)
                await uow.commit()
            published_committed = True

            logger.info(
                "知识库候选发布完成 build=%s kb=%s version=%s state=%s",
                build_id,
                kb_id,
                version_id,
                version_state.value,
            )
            yield build_message(
                message=(
                    f"文档知识库 **{kb.name}** 索引完成。\n\n"
                    f"- 本次处理文档: {len(manifest)}\n"
                    f"- 解析成功: {parsed_count}\n"
                    f"- 父块: {len(parents)}\n"
                    f"- 子块: {len(children)}"
                ),
            )
            yield build_done()
        except asyncio.CancelledError:
            await self._fail_candidate(build_id, "knowledge-base build cancelled")
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("知识库候选摄取失败")
            if published_committed:
                yield build_done()
                return
            await self._fail_candidate(build_id, str(exc))
            code = EMBEDDING_UNAVAILABLE if "embed" in str(exc).lower() else None
            yield build_error(message=str(exc), failure_code=code)

    async def _load_candidate(
        self,
        build_id: str,
    ) -> tuple[
        KnowledgeBaseVersion,
        list[KnowledgeVersionDocument],
        KnowledgeBase,
    ]:
        async with self._uow_factory() as uow:
            candidate = await uow.knowledge_version.get_build_candidate(build_id)
            if candidate is None:
                raise ValueError("knowledge-base build candidate not found")
            version, manifest = candidate
            kb = await uow.knowledge_base.get_kb(version.knowledge_base_id)
            if kb is None:
                raise ValueError("knowledge base not found")
        if not kb.owner_user_id:
            raise ValueError("knowledge base owner scope is malformed")
        return version, manifest, kb

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
                entry.document_id: await uow.knowledge_base.get_document_for_build(
                    entry.document_id
                )
                for entry in manifest
            }
        return revisions, documents

    async def _transition(
        self,
        version: KnowledgeBaseVersion,
        entry: KnowledgeVersionDocument,
        state: DocumentRevisionState,
        *,
        parsed_blocks: list[dict] | UnsetType | None = UNSET,
        page_count: int | UnsetType | None = UNSET,
        error: str | UnsetType | None = UNSET,
        warning: str | UnsetType | None = UNSET,
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
            await uow.commit()

    async def _checkpoint_candidate(
        self,
        version: KnowledgeBaseVersion,
        metrics: dict,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.knowledge_version.update_candidate_metrics(
                version.id,
                knowledge_base_id=version.knowledge_base_id,
                metrics=metrics,
            )
            await uow.commit()

    async def _fail_candidate(self, build_id: str, error: str) -> None:
        persistence_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self._uow_factory() as uow:
                    candidate_result = await uow.knowledge_version.get_build_candidate(build_id)
                    if candidate_result is None:
                        return
                    candidate, _manifest = candidate_result
                    kb = await uow.knowledge_base.get_kb(candidate.knowledge_base_id)
                    if kb is None:
                        return
                    if (
                        candidate.state
                        in {
                            KnowledgeVersionState.READY,
                            KnowledgeVersionState.DEGRADED,
                        }
                        and candidate.published_at is not None
                        and kb.active_version_id == candidate.id
                    ):
                        return
                    failed = candidate.state is KnowledgeVersionState.FAILED
                    if not failed:
                        failed = await uow.knowledge_version.fail_candidate(
                            candidate.id,
                            knowledge_base_id=candidate.knowledge_base_id,
                            metrics={"failed": 1},
                        )
                    if not failed:
                        # A publish/fail race changed the candidate after our
                        # read. Roll back and classify again in a fresh UoW.
                        raise RuntimeError("candidate terminal classification changed")
                    if kb.active_version_id != candidate.id:
                        kb.status = (
                            KBStatus.READY if kb.active_version_id is not None else KBStatus.FAILED
                        )
                        kb.error = error
                        kb.updated_at = datetime.now(UTC)
                        await uow.knowledge_base.save_kb(kb)
                    await uow.commit()
                persistence_error = None
                break
            except (OSError, RuntimeError, ValueError) as exc:
                persistence_error = exc
                logger.warning(
                    "候选失败状态持久化重试 build=%s attempt=%s: %s",
                    build_id,
                    attempt + 1,
                    exc,
                )
        if persistence_error is not None:
            raise RuntimeError("failed to persist candidate failure") from persistence_error

    async def _parse_document(
        self,
        doc: KnowledgeDocument,
        *,
        policy: KnowledgeBaseExecutionPolicy,
    ) -> tuple[list[PageBlock], int, str | None]:
        if doc.source_type == KBSourceType.WEB:
            web_doc = await self._web_documents.fetch(
                KBSourceType.WEB,
                doc.source_ref,
            )
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=policy.document.max_bytes,
                max_pages=policy.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if doc.source_type == KBSourceType.CONFLUENCE:
            web_doc = await self._web_documents.fetch(
                KBSourceType.CONFLUENCE,
                doc.source_ref,
            )
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=policy.document.max_bytes,
                max_pages=policy.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if doc.source_type == KBSourceType.FEISHU:
            web_doc = await self._web_documents.fetch(
                KBSourceType.FEISHU,
                doc.source_ref,
            )
            result = await parse_document(
                web_doc.content.encode("utf-8"),
                web_doc.mime,
                web_doc.title,
                max_bytes=policy.document.max_bytes,
                max_pages=policy.document.max_pages,
                ocr_mode="off",
            )
            return result.blocks, result.page_count, result.warning
        if not doc.file_id:
            raise ValueError("上传文档缺少 file_id")
        stream, file_info = await self._file_storage.download_file(doc.file_id)
        data = stream.read()
        if doc.source_type == KBSourceType.ZIP:
            return await self._parse_zip_document(
                data,
                file_info.filename,
                policy=policy,
            )
        result = await parse_document(
            data,
            file_info.mime_type,
            file_info.filename,
            max_bytes=policy.document.max_bytes,
            max_pages=policy.document.max_pages,
            ocr_mode=policy.ocr.mode,
            ocr_max_pages=policy.ocr.max_pages,
            expected_size=file_info.size,
        )
        blocks = result.blocks
        warning = result.warning
        if (
            policy.ocr.mode != "off"
            and (
                file_info.mime_type == "application/pdf"
                or file_info.filename.lower().endswith(".pdf")
            )
            and (not blocks or sum(len(block.text or "") for block in blocks) < 32)
        ):
            ocr_blocks, ocr_warning = await ocr_pdf_to_blocks(
                data,
                self._ocr_llm,
                max_pages=policy.ocr.max_pages,
            )
            if ocr_blocks:
                blocks = ocr_blocks
            if ocr_warning:
                warning = f"{warning}；{ocr_warning}" if warning else ocr_warning
        if sum(len(block.text or "") for block in blocks) == 0:
            raise ValueError(warning or "文档未提取到可索引文本")
        return blocks, result.page_count, warning

    async def _parse_zip_document(
        self,
        data: bytes,
        filename: str,
        *,
        policy: KnowledgeBaseExecutionPolicy,
    ) -> tuple[list[PageBlock], int, str | None]:
        blocks: list[PageBlock] = []
        warnings: list[str] = []
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            for name in names[: policy.document.max_pages]:
                child_data = archive.read(name)
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                try:
                    result = await parse_document(
                        child_data,
                        mime,
                        name,
                        max_bytes=policy.document.max_bytes,
                        max_pages=policy.document.max_pages,
                        ocr_mode=policy.ocr.mode,
                        ocr_max_pages=policy.ocr.max_pages,
                    )
                    for block in result.blocks:
                        blocks.append(
                            PageBlock(
                                page_no=len(blocks) + 1,
                                heading_path=(f"{filename}/{name}/{block.heading_path}"),
                                text=block.text,
                            )
                        )
                    if result.warning:
                        warnings.append(f"{name}: {result.warning}")
                except (OSError, RuntimeError, ValueError) as exc:
                    warnings.append(f"{name}: {exc}")
            if len(names) > policy.document.max_pages:
                warnings.append(
                    f"压缩包共 {len(names)} 个文件，仅解析前 {policy.document.max_pages} 个"
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
            raise TypeError("durable parsed block is not an object")
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

    Logical documents are immutable; replacements read their file/URL identity
    from the revision rather than the logical row.
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
            KBSourceType.ZIP if document.source_type is KBSourceType.ZIP else KBSourceType.UPLOAD
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
