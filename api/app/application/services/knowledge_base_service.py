#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import base64
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator, Awaitable, Callable, List, Optional

from pydantic import TypeAdapter

from app.application.dto.knowledge_build import (
    KnowledgeBuildProjection,
    KnowledgeVersionHistoryProjection,
    KnowledgeVersionProjection,
)
from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.domain.external.file_storage import FileStorage
from app.domain.models.event import BaseEvent, Event
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.knowledge_base import (
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphResponse,
)
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionState,
    mutable_json_value,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.domain.models.session import Session
from app.domain.models.codebase import SessionMode
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.knowledge_base_repository import DocumentPage
from app.domain.repositories.uow import IUnitOfWork
from app.domain.external.task_state_port import TaskStatePort
from app.domain.services.knowledge_base.url_guard import validate_public_url
from app.domain.services.knowledge_base.web_connector import (
    WebDocument,
    fetch_confluence_document,
    fetch_feishu_document,
    fetch_web_document,
)
from app.domain.services.knowledge_base.version_builder import (
    CandidateBuildResult,
    KnowledgeBuildCommand,
    KnowledgeBuildSource,
    KnowledgeVersionBuilder,
)
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import TaskStatus, get_task_state

logger = logging.getLogger(__name__)

_TERMINAL_KB_STATUSES = {KBStatus.READY, KBStatus.FAILED}
_INGEST_STALE_AFTER_SECONDS = 120.0
_DELETE_TASK_DRAIN_TIMEOUT_SECONDS = 30.0
_DELETE_TASK_POLL_INTERVAL_SECONDS = 0.5


class KnowledgeBaseService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
            resource_guard: Optional[ResourceGuardService] = None,
            resource_binding_service: Optional[ResourceBindingService] = None,
            version_builder: Optional[KnowledgeVersionBuilder] = None,
            build_dispatcher: Optional[
                Callable[[str, str], Awaitable[None]]
            ] = None,
            web_fetcher: Optional[
                Callable[[str], Awaitable[WebDocument]]
            ] = None,
            confluence_fetcher: Optional[
                Callable[[str], Awaitable[WebDocument]]
            ] = None,
            feishu_fetcher: Optional[
                Callable[[str], Awaitable[WebDocument]]
            ] = None,
            task_state_port: Optional[TaskStatePort] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._task_state = task_state_port or get_task_state()
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service
        self._version_builder = version_builder or KnowledgeVersionBuilder(
            uow_factory
        )
        self._build_dispatcher = (
            build_dispatcher or self._dispatch_candidate_task
        )
        self._web_fetcher = web_fetcher or fetch_web_document
        self._confluence_fetcher = (
            confluence_fetcher or fetch_confluence_document
        )
        self._feishu_fetcher = feishu_fetcher or fetch_feishu_document

    async def _ingest_in_progress(self, kb: KnowledgeBase) -> bool:
        if not kb.ingest_task_id:
            return False
        if kb.status in _TERMINAL_KB_STATUSES:
            return False
        meta = await self._task_state.get_task_meta(kb.ingest_task_id)
        if not meta:
            return False
        if await self._task_state.is_done(kb.ingest_task_id):
            return False
        if self._task_state.heartbeat_is_stale(meta, _INGEST_STALE_AFTER_SECONDS):
            return False
        return True

    async def _wait_for_task_drain(self, task_id: str) -> None:
        deadline = time.monotonic() + _DELETE_TASK_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            snapshot = await self._task_state.get_runtime_snapshot(task_id)
            if snapshot.get("is_done"):
                return
            await asyncio.sleep(_DELETE_TASK_POLL_INTERVAL_SECONDS)
        logger.warning("等待知识库摄取任务结束超时 task_id=%s", task_id)

    async def _retire_ingest_task(self, task_id: str) -> None:
        try:
            meta = await self._task_state.get_task_meta(task_id)
            if not meta:
                return
            await self._task_state.set_status(
                task_id,
                int(meta.get("run_generation", 1)),
                TaskStatus.FAILED,
            )
        except Exception as exc:
            logger.warning("标记旧知识库摄取任务失败 task_id=%s: %s", task_id, exc)

    @staticmethod
    def _infer_file_source_type(filename: str, mime: str, fallback: KBSourceType) -> KBSourceType:
        lower = (filename or "").lower()
        if lower.endswith(".zip"):
            return KBSourceType.ZIP
        if fallback == KBSourceType.ZIP and not lower.endswith(".zip"):
            return KBSourceType.UPLOAD
        return fallback

    async def _dispatch_created_build(
        self,
        result: CandidateBuildResult,
    ) -> None:
        if not result.created:
            return
        try:
            await self._build_dispatcher(
                result.build.id,
                result.resource.id,
            )
        except Exception as exc:
            # The build/candidate/manifest transaction is already committed.
            # Worker reconciliation owns redelivery; callers still receive the
            # durable queued identity and must not manufacture a duplicate.
            logger.warning(
                "知识库候选构建派发失败，保留 queued build 供恢复 "
                "build_id=%s kb_id=%s: %s",
                result.build.id,
                result.resource.id,
                exc,
            )

    async def _dispatch_candidate_task(
        self,
        build_id: str,
        kb_id: str,
    ) -> None:
        await self._task_state.register_task(
            build_id,
            session_id=f"kb-ingest:{kb_id}",
            task_type="kb_ingest",
            resource_id=kb_id,
        )
        task = RedisStreamTask(
            task_id=build_id,
            session_id=f"kb-ingest:{kb_id}",
        )
        await task.dispatch_to_worker()

    async def _fetch_url_document(
        self,
        source_type: KBSourceType,
        url: str,
    ) -> WebDocument:
        fetchers: dict[
            KBSourceType,
            Callable[[str], Awaitable[WebDocument]],
        ] = {
            KBSourceType.WEB: self._web_fetcher,
            KBSourceType.CONFLUENCE: self._confluence_fetcher,
            KBSourceType.FEISHU: self._feishu_fetcher,
        }
        fetcher = fetchers.get(source_type)
        if fetcher is None:
            raise BadRequestError(
                "URL 只能使用 web、confluence 或 feishu 来源类型"
            )
        try:
            return await fetcher(url)
        except Exception as exc:
            raise BadRequestError(f"URL[{url}]无法下载: {exc}") from exc

    async def _prepare_reindex_sources(
        self,
        kb: KnowledgeBase,
        *,
        scope: OwnerScope,
    ) -> list[KnowledgeBuildSource]:
        if kb.active_version_id is None:
            return []
        async with self._uow_factory() as uow:
            manifest = await uow.knowledge_version.get_manifest(
                kb.active_version_id,
                knowledge_base_id=kb.id,
            )
            revisions = await uow.knowledge_version.get_revisions(
                [entry.document_revision_id for entry in manifest],
                knowledge_base_id=kb.id,
            )
            documents: list[KnowledgeDocument] = []
            for entry in manifest:
                document = await uow.knowledge_base.get_document(
                    entry.document_id
                )
                if document is None or document.kb_id != kb.id:
                    raise NotFoundError(
                        "document not found in knowledge base manifest"
                    )
                documents.append(document)
        sources: list[KnowledgeBuildSource] = []
        for entry, document in zip(manifest, documents):
            revision: KnowledgeDocumentRevision | None = revisions.get(
                entry.document_revision_id
            )
            if revision is None:
                raise ConflictError(
                    "knowledge manifest revision closure is incomplete"
                )
            digest = revision.source_digest
            source_ref = revision.source_ref or document.source_ref
            source_type = document.source_type
            source_title = document.title
            source_mime = document.mime
            source_file_id = (
                _file_id_from_source_ref(source_ref)
                or document.file_id
            )
            if source_file_id:
                async with self._uow_factory() as uow:
                    file_info = await uow.file.get_by_id(
                        source_file_id,
                        scope=scope,
                    )
                if file_info is None:
                    raise BadRequestError(
                        f"文件[{source_file_id}]不存在或无权访问"
                    )
                try:
                    stream, stored_file = (
                        await self._file_storage.download_file(
                            source_file_id
                        )
                    )
                except Exception as exc:
                    raise BadRequestError(
                        f"文件[{source_file_id}]不存在或无法下载: {exc}"
                    ) from exc
                if stored_file.id != file_info.id:
                    raise BadRequestError(
                        f"文件[{source_file_id}]不存在或无法下载"
                    )
                digest = _sha256_stream(stream)
                identity = f"file:{source_file_id}"
                source_title = file_info.filename
                source_mime = file_info.mime_type
                source_type = self._infer_file_source_type(
                    file_info.filename,
                    file_info.mime_type,
                    (
                        document.source_type
                        if document.source_type
                        in {KBSourceType.UPLOAD, KBSourceType.ZIP}
                        else KBSourceType.UPLOAD
                    ),
                )
            else:
                source_ref = validate_public_url(source_ref)
                web_document = await self._fetch_url_document(
                    source_type,
                    source_ref,
                )
                digest = _sha256_text(web_document.content)
                source_title = web_document.title or source_title
                source_mime = web_document.mime
                identity = (
                    f"{source_type.value}:{source_ref.strip()}"
                )
            sources.append(
                KnowledgeBuildSource(
                    document_id=document.id,
                    title=source_title,
                    source_type=source_type,
                    source_ref=source_ref,
                    source_identity=identity,
                    source_digest=digest,
                    mime=source_mime,
                    file_id=source_file_id,
                )
            )
        return sources

    async def create_kb(
            self,
            name: str = "未命名知识库",
            settings: Optional[dict] = None,
            scope: Optional[OwnerScope] = None,
    ) -> KnowledgeBase:
        kb = KnowledgeBase(
            name=name or "未命名知识库",
            settings=settings or {},
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
        )
        async with self._uow_factory() as uow:
            await uow.knowledge_base.save_kb(kb)
        return kb

    async def add_documents(
            self,
            kb_id: str,
            *,
            file_ids: Optional[List[str]] = None,
            urls: Optional[List[str]] = None,
            source_type: KBSourceType = KBSourceType.UPLOAD,
            scope: Optional[OwnerScope] = None,
    ) -> KnowledgeBase:
        file_ids = file_ids or []
        urls = urls or []
        if not file_ids and not urls:
            raise BadRequestError("请至少上传一个文件或提供一个 URL")
        if file_ids and source_type not in {
            KBSourceType.UPLOAD,
            KBSourceType.ZIP,
        }:
            raise BadRequestError(
                "文件只能使用 upload 或 zip 来源类型"
            )
        if urls and source_type not in {
            KBSourceType.WEB,
            KBSourceType.CONFLUENCE,
            KBSourceType.FEISHU,
        }:
            raise BadRequestError(
                "URL 只能使用 web、confluence 或 feishu 来源类型"
            )

        kb = await self.get_kb(kb_id, scope=scope)
        actor_id = _require_actor(scope, kb)
        sources: list[KnowledgeBuildSource] = []
        for file_id in file_ids:
            async with self._uow_factory() as uow:
                file_info = await uow.file.get_by_id(file_id, scope=scope)
            if file_info is None:
                raise BadRequestError(f"文件[{file_id}]不存在或无权访问")
            try:
                stream, stored_file = await self._file_storage.download_file(
                    file_id
                )
            except Exception as exc:
                raise BadRequestError(f"文件[{file_id}]不存在或无法下载: {exc}") from exc
            if stored_file.id != file_info.id:
                raise BadRequestError(
                    f"文件[{file_id}]不存在或无法下载"
                )
            inferred = self._infer_file_source_type(file_info.filename, file_info.mime_type, source_type)
            sources.append(
                KnowledgeBuildSource(
                    title=file_info.filename,
                    source_type=inferred,
                    source_ref=json.dumps(
                        {"file_id": file_id},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    source_identity=f"file:{file_id}",
                    source_digest=_sha256_stream(stream),
                    mime=file_info.mime_type,
                    file_id=file_id,
                )
            )
        for url in urls:
            safe_url = validate_public_url(url)
            resolved_type = source_type
            source_title = safe_url
            source_mime = "text/markdown"
            web_document = await self._fetch_url_document(
                resolved_type,
                safe_url,
            )
            source_title = web_document.title or safe_url
            source_mime = web_document.mime
            sources.append(
                KnowledgeBuildSource(
                    title=source_title,
                    source_type=resolved_type,
                    source_ref=safe_url,
                    source_identity=f"{resolved_type.value}:{safe_url}",
                    source_digest=_sha256_text(web_document.content),
                    mime=source_mime,
                )
            )
        result = await self._version_builder.create_candidate(
            KnowledgeBuildCommand.add(
                kb_id,
                sources,
                actor_id=actor_id,
            ),
            scope=_require_scope(scope, kb),
        )
        await self._dispatch_created_build(result)
        return result.resource

    async def get_kb(self, kb_id: str, scope: Optional[OwnerScope] = None) -> KnowledgeBase:
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb:
                counts = await uow.knowledge_base.count_ready_documents([kb_id])
                kb.ready_doc_count = counts.get(kb_id, 0)
        if not kb:
            raise NotFoundError(f"知识库[{kb_id}]不存在")
        return kb

    async def get_version_graph(
            self,
            kb_id: str,
            version_id: str,
            *,
            q: Optional[str] = None,
            cursor: Optional[str] = None,
            limit: int = 50,
            scope: Optional[OwnerScope] = None,
    ) -> KnowledgeGraphResponse:
        """Read one owner-scoped, exact published graph page."""
        if not kb_id.strip() or not version_id.strip():
            raise BadRequestError("knowledge graph identity cannot be empty")
        bounded_limit = max(1, min(int(limit), 100))
        normalized_query = (q or "").strip()
        if len(normalized_query) > 200:
            raise BadRequestError("knowledge graph query is too long")
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb is None:
                raise NotFoundError(f"知识库[{kb_id}]不存在")
            version = await uow.knowledge_version.get_version(
                version_id,
                knowledge_base_id=kb_id,
            )
            if version is None:
                raise NotFoundError("knowledge base version not found")
            if (
                version.knowledge_base_id != kb_id
                or version.published_at is None
                or version.state
                not in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
            ):
                raise BadRequestError(
                    "knowledge base version is not published"
                )
            after = (
                _decode_graph_page_cursor(
                    cursor,
                    kb_id=kb_id,
                    version_id=version_id,
                    query=normalized_query,
                )
                if cursor
                else None
            )
            if not bool(version.capabilities.get("graph_search", False)):
                return KnowledgeGraphResponse(
                    capability=False,
                    nodes=(),
                    edges=(),
                    next_cursor=None,
                )
            entities, next_key = (
                await uow.knowledge_base.list_entities_page_for_version(
                    kb_id,
                    version_id,
                    q=normalized_query or None,
                    after=after,
                    limit=bounded_limit,
                )
            )
            relations = (
                await uow.knowledge_base
                .list_relations_for_entities_for_version(
                    kb_id,
                    version_id,
                    [entity.id for entity in entities],
                )
            )
            known_ids = {entity.id for entity in entities}
            endpoint_ids = {
                endpoint_id
                for relation in relations
                for endpoint_id in (
                    relation.src_entity_id,
                    relation.dst_entity_id,
                )
            }
            missing = sorted(endpoint_ids - known_ids)
            if missing:
                entities.extend(
                    await uow.knowledge_base
                    .get_entities_by_ids_for_version(
                        kb_id,
                        version_id,
                        missing,
                    )
                )
            entity_by_id = {entity.id: entity for entity in entities}
            safe_relations = [
                relation
                for relation in relations
                if relation.src_entity_id in entity_by_id
                and relation.dst_entity_id in entity_by_id
            ]
            evidence_rows = (
                await uow.knowledge_base.get_chunks_by_ids_for_version(
                    kb_id,
                    version_id,
                    sorted(
                        {
                            relation.chunk_id
                            for relation in safe_relations
                            if relation.chunk_id
                        }
                    ),
                )
            )
        evidence_by_chunk = {
            row.chunk.id: KnowledgeCitation(
                version_id=version_id,
                document_revision_id=row.document_revision_id,
                doc_id=row.document.id,
                page_no=row.chunk.page_no,
                chunk_id=row.chunk.id,
            )
            for row in evidence_rows
            if (
                row.chunk.kb_id == kb_id
                and row.chunk.version_id == version_id
                and row.document.kb_id == kb_id
            )
        }
        nodes = tuple(
            KnowledgeGraphNode(
                id=entity.id,
                name=entity.name,
                type=entity.type,
                description=entity.description,
            )
            for entity in sorted(
                entity_by_id.values(),
                key=lambda item: (
                    item.normalized_name,
                    item.id,
                ),
            )
        )
        edges = tuple(
            KnowledgeGraphEdge(
                id=relation.id,
                source=relation.src_entity_id,
                target=relation.dst_entity_id,
                relation=relation.relation,
                evidence=(
                    (evidence_by_chunk[relation.chunk_id],)
                    if relation.chunk_id in evidence_by_chunk
                    else ()
                ),
            )
            for relation in sorted(
                safe_relations,
                key=lambda item: (item.relation, item.id),
            )
        )
        return KnowledgeGraphResponse(
            nodes=nodes,
            edges=edges,
            capability=True,
            next_cursor=(
                _encode_graph_page_cursor(
                    kb_id,
                    version_id,
                    normalized_query,
                    next_key,
                )
                if next_key is not None
                else None
            ),
        )

    async def list_kbs(self, limit: int = 100, offset: int = 0, scope: Optional[OwnerScope] = None) -> List[KnowledgeBase]:
        async with self._uow_factory() as uow:
            kbs = await uow.knowledge_base.list_kbs(limit=limit, offset=offset, scope=scope)
            counts = await uow.knowledge_base.count_ready_documents([kb.id for kb in kbs])
        for kb in kbs:
            kb.ready_doc_count = counts.get(kb.id, 0)
        return kbs

    async def list_documents(
            self,
            kb_id: str,
            limit: int = 50,
            offset: int = 0,
            scope: Optional[OwnerScope] = None,
    ) -> tuple[List[KnowledgeDocument], int]:
        await self.get_kb(kb_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.knowledge_base.list_documents_page(kb_id, limit=limit, offset=offset)

    async def _dispatch_ingest(self, kb: KnowledgeBase) -> KnowledgeBase:
        old_task_id = kb.ingest_task_id
        if old_task_id:
            await self._retire_ingest_task(old_task_id)
        task_id = str(uuid.uuid4())
        await self._task_state.register_task(
            task_id,
            session_id=f"kb-ingest:{kb.id}",
            task_type="kb_ingest",
            resource_id=kb.id,
        )
        kb.ingest_task_id = task_id
        kb.status = KBStatus.PENDING
        kb.error = None
        kb.updated_at = datetime.now()
        async with self._uow_factory() as uow:
            await uow.knowledge_base.save_kb(kb)
        task = RedisStreamTask(task_id=task_id, session_id=f"kb-ingest:{kb.id}")
        await task.dispatch_to_worker()
        return kb

    async def _create_reindex_candidate(
        self,
        kb_id: str,
        *,
        scope: Optional[OwnerScope],
    ) -> CandidateBuildResult:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        sources = await self._prepare_reindex_sources(
            kb,
            scope=normalized_scope,
        )
        result = await self._version_builder.create_candidate(
            KnowledgeBuildCommand.reindex(
                kb_id,
                sources,
                actor_id=_require_actor(normalized_scope, kb),
            ),
            scope=normalized_scope,
        )
        await self._dispatch_created_build(result)
        return result

    async def reindex(self, kb_id: str, scope: Optional[OwnerScope] = None) -> KnowledgeBase:
        result = await self._create_reindex_candidate(
            kb_id,
            scope=scope,
        )
        return result.resource

    async def list_versions(
        self,
        kb_id: str,
        *,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeVersionHistoryProjection:
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb is None:
                raise NotFoundError(f"知识库[{kb_id}]不存在")
            versions = await uow.knowledge_version.list_versions(
                kb.id,
                limit=500,
            )
            projected = tuple(
                [
                    await self._project_version(uow, kb, version)
                    for version in versions
                ]
            )
            active_build = (
                await uow.resource_governance.get_active_build(
                    ResourceKind.KNOWLEDGE_BASE,
                    kb.id,
                )
            )
            active_projection = None
            if active_build is not None:
                active_projection = await self._project_build(
                    uow,
                    kb.id,
                    active_build,
                    active_version_id=kb.active_version_id,
                )
                if not any(
                    version.id == active_build.version_id
                    and version.build_id == active_build.id
                    for version in projected
                ):
                    raise ConflictError(
                        "active knowledge build has no matching version"
                    )
        return KnowledgeVersionHistoryProjection(
            knowledge_base_id=kb.id,
            active_version_id=kb.active_version_id,
            active_build=active_projection,
            versions=projected,
        )

    async def get_version(
        self,
        kb_id: str,
        version_id: str,
        *,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeVersionProjection:
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb is None:
                raise NotFoundError(f"知识库[{kb_id}]不存在")
            version = await uow.knowledge_version.get_version(
                version_id,
                knowledge_base_id=kb.id,
            )
            if version is None:
                raise NotFoundError(
                    "knowledge base version not found in owner scope"
                )
            return await self._project_version(uow, kb, version)

    async def create_build(
        self,
        kb_id: str,
        *,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeVersionProjection:
        result = await self._create_reindex_candidate(
            kb_id,
            scope=scope,
        )
        return self._project_candidate_result(result)

    async def retry_build(
        self,
        kb_id: str,
        build_id: str,
        *,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeVersionProjection:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        result = await self._version_builder.retry_candidate(
            kb.id,
            build_id,
            actor_id=_require_actor(normalized_scope, kb),
            scope=normalized_scope,
        )
        await self._dispatch_created_build(result)
        return self._project_candidate_result(result)

    async def cancel_build(
        self,
        kb_id: str,
        build_id: str,
        *,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeBuildProjection:
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb is None:
                raise NotFoundError("knowledge build not found in owner scope")
            build = await uow.resource_governance.get_build(
                build_id,
                for_update=True,
            )
            if (
                build is None
                or build.resource_kind is not ResourceKind.KNOWLEDGE_BASE
                or build.resource_id != kb.id
            ):
                raise NotFoundError("knowledge build not found in owner scope")
            if build.state not in {BuildState.QUEUED, BuildState.RUNNING}:
                raise ConflictError(
                    "only an active knowledge build can be cancelled"
                )
            candidate = await uow.knowledge_version.get_version(
                build.version_id,
                knowledge_base_id=kb.id,
            )
            if (
                candidate is None
                or candidate.build_id != build.id
                or candidate.parent_version_id != build.parent_version_id
                or build.parent_version_id != kb.active_version_id
                or candidate.state is not KnowledgeVersionState.BUILDING
                or candidate.published_at is not None
            ):
                raise ConflictError(
                    "knowledge build candidate closure is malformed"
                )
            projection = self._build_projection(
                build,
                active_version_id=kb.active_version_id,
            )
        await self._task_state.request_cancel(build.id)
        return projection

    async def _project_version(
        self,
        uow: IUnitOfWork,
        kb: KnowledgeBase,
        version: KnowledgeBaseVersion,
    ) -> KnowledgeVersionProjection:
        is_published = (
            version.published_at is not None
            and version.state
            in {KnowledgeVersionState.READY, KnowledgeVersionState.DEGRADED}
        )
        if (
            version.published_at is None
            and version.state
            in {KnowledgeVersionState.READY, KnowledgeVersionState.DEGRADED}
        ) or (
            version.published_at is not None
            and version.state
            not in {KnowledgeVersionState.READY, KnowledgeVersionState.DEGRADED}
        ):
            raise ConflictError("knowledge version publication is malformed")
        if version.id == kb.active_version_id and not is_published:
            raise ConflictError("knowledge base active version is not published")
        build_projection = None
        if version.build_id is not None:
            build = await uow.resource_governance.get_build(version.build_id)
            if build is None:
                raise ConflictError(
                    "knowledge version has no matching build"
                )
            build_projection = await self._project_build(
                uow,
                kb.id,
                build,
                active_version_id=kb.active_version_id,
            )
            if (
                build.version_id != version.id
                or build.parent_version_id != version.parent_version_id
            ):
                raise ConflictError(
                    "knowledge version build closure is malformed"
                )
        return KnowledgeVersionProjection(
            id=version.id,
            knowledge_base_id=version.knowledge_base_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state,
            capabilities=version.capabilities,
            degraded_reasons=version.degraded_reasons,
            metrics=version.metrics,
            legacy_snapshot=version.legacy_snapshot,
            created_at=version.created_at,
            published_at=version.published_at,
            is_active=version.id == kb.active_version_id,
            is_published=is_published,
            is_candidate=not is_published,
            build=build_projection,
        )

    async def _project_build(
        self,
        uow: IUnitOfWork,
        kb_id: str,
        build: ResourceBuild,
        *,
        active_version_id: str | None,
    ) -> KnowledgeBuildProjection:
        if (
            build.resource_kind is not ResourceKind.KNOWLEDGE_BASE
            or build.resource_id != kb_id
        ):
            raise ConflictError("knowledge build owner closure is malformed")
        candidate = await uow.knowledge_version.get_version(
            build.version_id,
            knowledge_base_id=kb_id,
        )
        if (
            candidate is None
            or candidate.build_id != build.id
            or candidate.parent_version_id != build.parent_version_id
            or (
                build.state in {BuildState.QUEUED, BuildState.RUNNING}
                and build.parent_version_id != active_version_id
            )
        ):
            raise ConflictError(
                "knowledge build candidate closure is malformed"
            )
        return self._build_projection(
            build,
            active_version_id=active_version_id,
        )

    @staticmethod
    def _build_projection(
        build: ResourceBuild,
        *,
        active_version_id: str | None,
    ) -> KnowledgeBuildProjection:
        return KnowledgeBuildProjection(
            id=build.id,
            knowledge_base_id=build.resource_id,
            version_id=build.version_id,
            parent_version_id=build.parent_version_id,
            command_key=build.command_key,
            state=build.state,
            phase=build.phase,
            progress=build.progress,
            capabilities=tuple(build.capabilities),
            degraded_reasons=tuple(build.degraded_reasons),
            metrics=mutable_json_value(build.metrics),
            error_code=build.error_code,
            error_message=build.error_message,
            heartbeat_at=build.heartbeat_at,
            last_event_seq=build.last_event_seq,
            created_at=build.created_at,
            started_at=build.started_at,
            finished_at=build.finished_at,
            can_retry=build.state
            in {BuildState.FAILED, BuildState.CANCELLED}
            and build.parent_version_id == active_version_id,
            can_cancel=build.state
            in {BuildState.QUEUED, BuildState.RUNNING},
        )

    @classmethod
    def _project_candidate_result(
        cls,
        result: CandidateBuildResult,
    ) -> KnowledgeVersionProjection:
        version = result.version
        return KnowledgeVersionProjection(
            id=version.id,
            knowledge_base_id=version.knowledge_base_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state,
            capabilities=version.capabilities,
            degraded_reasons=version.degraded_reasons,
            metrics=version.metrics,
            legacy_snapshot=version.legacy_snapshot,
            created_at=version.created_at,
            published_at=version.published_at,
            is_active=version.id == result.resource.active_version_id,
            is_published=False,
            is_candidate=True,
            build=cls._build_projection(
                result.build,
                active_version_id=result.resource.active_version_id,
            ),
        )

    async def stream_ingest(
            self,
            kb_id: str,
            latest_event_id: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        kb = await self.get_kb(kb_id, scope=scope)
        if not kb.ingest_task_id:
            return
        output = RedisStreamMessageQueue(f"task:output:{kb.ingest_task_id}")
        cursor = latest_event_id or "0"
        adapter = TypeAdapter(Event)
        while True:
            if await self._task_state.is_cancelled(kb.ingest_task_id):
                break
            event_id, event_str = await output.get(start_id=cursor, block_ms=1000)
            if event_str is not None:
                cursor = event_id
                event_payload = json.loads(event_str)
                event = adapter.validate_python(upgrade_event_payload(event_payload))
                event.id = event_id
                yield event
                if event.type in {"done", "error"}:
                    return
                continue
            if await self._task_state.is_done(kb.ingest_task_id):
                return

    async def create_session_for_kb(
            self,
            kb_id: str,
            mode: SessionMode = SessionMode.ASK,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            knowledge_base_version_id: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> Session:
        kb = await self.get_kb(kb_id, scope=scope)
        validated = None
        if self._resource_guard and scope:
            validated = await self._resource_guard.validate_session_request(
                mode=mode,
                codebase_id=None,
                codebase_version_id=None,
                knowledge_base_id=kb_id,
                knowledge_base_version_id=knowledge_base_version_id,
                scope=scope,
            )
        elif kb.ready_doc_count <= 0:
            raise BadRequestError("知识库尚无就绪文档，请等待索引完成后再开始问答")
        session = Session(
            title=f"文档知识库对话 · {kb.name}",
            knowledge_base_id=kb_id,
            mode=mode,
            model_id=model_id,
            skill_id=skill_id,
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
        )
        async with self._uow_factory() as uow:
            await uow.session.save(session)
            if validated and self._resource_binding_service and scope:
                binding = await self._resource_binding_service.bind_initial_resolved(
                    uow, session_id=session.id, resolved=validated.knowledge_base,
                    scope=scope, actor_id=scope.user_id,
                )
                session.resource_bindings.append(binding.to_projection())
        return session

    async def read_document(
            self,
            doc_id: str,
            page: Optional[int] = None,
            limit: int = 30,
            *,
            kb_id: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> tuple[KnowledgeDocument, str]:
        async with self._uow_factory() as uow:
            doc = await uow.knowledge_base.get_document(doc_id)
            if not doc:
                raise NotFoundError(f"文档[{doc_id}]不存在")
            if kb_id and doc.kb_id != kb_id:
                raise NotFoundError(f"文档[{doc_id}]不属于知识库[{kb_id}]")
            if await uow.knowledge_base.get_kb(doc.kb_id, scope=scope) is None:
                raise NotFoundError(f"知识库[{doc.kb_id}]不存在")
            chunks = await uow.knowledge_base.list_chunks_for_document(doc_id, page_no=page, limit=limit)
        content = "\n\n".join(chunk.content for chunk in chunks if chunk.level.value == "parent")
        return doc, content

    async def read_document_page(
            self,
            kb_id: str,
            version_id: str,
            doc_id: str,
            *,
            page: Optional[int] = None,
            cursor: Optional[str] = None,
            limit: int = 30,
            scope: Optional[OwnerScope] = None,
    ) -> tuple[KnowledgeDocument, str, DocumentPage]:
        if page is not None and (
            isinstance(page, bool) or not isinstance(page, int) or page < 1
        ):
            raise BadRequestError("页码必须大于等于 1")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 200
        ):
            raise BadRequestError("分页大小必须在 1 到 200 之间")
        await self.get_kb(kb_id, scope=scope)
        async with self._uow_factory() as uow:
            resolved = (
                await uow.knowledge_base.get_document_for_version(
                    kb_id,
                    version_id,
                    doc_id,
                )
            )
            if resolved is None:
                raise NotFoundError(
                    f"版本[{version_id}]中不存在可读取文档[{doc_id}]"
                )
            document, revision_id = resolved
            try:
                source_page = (
                    await uow.knowledge_base
                    .read_document_page_for_version(
                        kb_id,
                        version_id,
                        doc_id,
                        revision_id,
                        page_no=page,
                        cursor=cursor,
                        limit=limit,
                    )
                )
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
        return document, revision_id, source_page

    async def delete_kb(self, kb_id: str, scope: Optional[OwnerScope] = None) -> None:
        kb = await self.get_kb(kb_id, scope=scope)
        if await self._ingest_in_progress(kb):
            raise ConflictError(
                "知识库正在索引中，请等待当前任务完成后再删除",
                error_key="errors.kbIndexingInProgress",
            )
        if kb.ingest_task_id:
            await self._task_state.request_cancel(kb.ingest_task_id)
            await self._wait_for_task_drain(kb.ingest_task_id)
        async with self._uow_factory() as uow:
            await uow.knowledge_base.delete_kb(kb_id)
        logger.info("删除知识库[%s]成功", kb_id)

    async def delete_document(
            self,
            kb_id: str,
            doc_id: str,
            scope: Optional[OwnerScope] = None,
    ) -> KnowledgeBase:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        result = await self._version_builder.create_candidate(
            KnowledgeBuildCommand.remove(
                kb_id,
                doc_id,
                actor_id=_require_actor(normalized_scope, kb),
            ),
            scope=normalized_scope,
        )
        await self._dispatch_created_build(result)
        return result.resource

    async def replace_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        file_id: str,
        source_type: KBSourceType = KBSourceType.UPLOAD,
        scope: Optional[OwnerScope] = None,
    ) -> KnowledgeBase:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        async with self._uow_factory() as uow:
            file_info = await uow.file.get_by_id(
                file_id,
                scope=normalized_scope,
            )
        if file_info is None:
            raise BadRequestError(
                f"文件[{file_id}]不存在或无权访问"
            )
        try:
            stream, stored_file = await self._file_storage.download_file(
                file_id
            )
        except Exception as exc:
            raise BadRequestError(
                f"文件[{file_id}]不存在或无法下载: {exc}"
            ) from exc
        if stored_file.id != file_info.id:
            raise BadRequestError(f"文件[{file_id}]不存在或无法下载")
        inferred = self._infer_file_source_type(
            file_info.filename,
            file_info.mime_type,
            source_type,
        )
        source = KnowledgeBuildSource(
            document_id=doc_id,
            title=file_info.filename,
            source_type=inferred,
            source_ref=json.dumps(
                {"file_id": file_id},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_identity=f"file:{file_id}",
            source_digest=_sha256_stream(stream),
            mime=file_info.mime_type,
            file_id=file_id,
        )
        result = await self._version_builder.create_candidate(
            KnowledgeBuildCommand.replace(
                kb_id,
                doc_id,
                source,
                actor_id=_require_actor(normalized_scope, kb),
            ),
            scope=normalized_scope,
        )
        await self._dispatch_created_build(result)
        return result.resource


def _encode_graph_page_cursor(
    kb_id: str,
    version_id: str,
    query: str,
    key: tuple[str, str],
) -> str:
    raw = json.dumps(
        {
            "kb": kb_id,
            "version": version_id,
            "q": query,
            "name": key[0],
            "id": key[1],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_graph_page_cursor(
    value: str,
    *,
    kb_id: str,
    version_id: str,
    query: str,
) -> tuple[str, str]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(value + padding).decode()
        )
        if (
            payload.get("kb") != kb_id
            or payload.get("version") != version_id
            or payload.get("q") != query
        ):
            raise ValueError
        name = payload["name"]
        entity_id = payload["id"]
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(entity_id, str)
            or not entity_id
        ):
            raise ValueError
        return name, entity_id
    except Exception as exc:
        raise BadRequestError("invalid knowledge graph cursor") from exc


def _require_scope(
    scope: Optional[OwnerScope],
    kb: KnowledgeBase,
) -> OwnerScope:
    if scope is not None:
        return scope
    if kb.team_id:
        raise NotFoundError("knowledge base not found in owner scope")
    if kb.owner_user_id:
        return OwnerScope.personal(kb.owner_user_id)
    raise NotFoundError("knowledge base not found in owner scope")


def _require_actor(
    scope: Optional[OwnerScope],
    kb: KnowledgeBase,
) -> str:
    return _require_scope(scope, kb).user_id


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_id_from_source_ref(source_ref: str) -> str | None:
    try:
        payload = json.loads(source_ref)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    file_id = payload.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        return None
    return file_id.strip()


def _sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    try:
        stream.seek(0)
    except (AttributeError, OSError):
        pass
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        digest.update(chunk)
    try:
        stream.seek(0)
    except (AttributeError, OSError):
        pass
    return digest.hexdigest()
