import base64
import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncGenerator, Callable
from functools import partial

from app.application.dto.knowledge_build import (
    KnowledgeBuildProjection,
    KnowledgeVersionHistoryProjection,
    KnowledgeVersionProjection,
)
from app.application.execution.admission import RunAdmissionService
from app.application.execution.public_projection import PublicExecutionEvent
from app.application.execution.run_control import RunControlService
from app.application.ports.queries import (
    ResourceBuildView,
    RunProjectionPort,
)
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.domain.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.execution.run import RunFamily, RunStatus
from app.domain.external.file_storage import FileStorage
from app.domain.external.web_document import WebDocument, WebDocumentGateway
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
    InferencePurpose,
)
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
)
from app.domain.models.resource_bindings import (
    ResourceBuildIntent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session
from app.domain.models.session_mode import SessionMode
from app.domain.repositories.knowledge_base_repository import DocumentPage
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import ExecutionPolicy
from app.domain.services.knowledge_base.url_guard import validate_public_url
from app.domain.services.knowledge_base.version_builder import (
    CandidateBuildResult,
    KnowledgeBuildCommand,
    KnowledgeBuildSource,
    KnowledgeVersionBuilder,
)

logger = logging.getLogger(__name__)

_TERMINAL_KB_STATUSES = {KBStatus.READY, KBStatus.FAILED}
_PUBLISHED_KB_VERSION_STATES = {
    KnowledgeVersionState.READY,
    KnowledgeVersionState.DEGRADED,
}


class KnowledgeBaseService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        file_storage: FileStorage,
        run_admission_service: RunAdmissionService,
        run_control_service: RunControlService,
        run_projection: RunProjectionPort,
        web_documents: WebDocumentGateway,
        resource_guard: ResourceGuardService | None = None,
        resource_binding_service: ResourceBindingService | None = None,
        version_builder: KnowledgeVersionBuilder | None = None,
        inference_bindings: InferenceBindingService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._run_admission = run_admission_service
        self._run_control = run_control_service
        self._run_projection = run_projection
        self._web_documents = web_documents
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service
        self._version_builder = version_builder or KnowledgeVersionBuilder(uow_factory)
        self._inference_bindings = inference_bindings

    async def _admit_build(
        self,
        result: CandidateBuildResult,
        scope: OwnerScope,
        *,
        uow: IUnitOfWork,
        command_sink=None,
    ) -> None:
        build = result.build

        async def private_input(policy: ExecutionPolicy):
            # Freeze the embedding binding through the caller's UoW. This closure
            # runs inside admit(), which runs inside the outer build UoW, so it
            # must NOT open a second UoW (P1-2 nested-UoW pool-exhaustion vector):
            # reuse ``uow``'s connection instead of InferenceBindingService.resolve,
            # which would check out a second pooled connection.
            frozen = await self._freeze_embedding(build, scope, policy=policy, uow=uow)
            return {
                "build_id": frozen.build_id,
                "resource_id": frozen.resource_id,
                "version_id": frozen.version_id,
                "embedding_model_id": frozen.embedding_model_id,
                "embedding_dimensions": frozen.embedding_dimensions,
            }

        await self._run_admission.admit(
            family=RunFamily.KB_INGEST,
            source_entity_type="resource_build",
            source_entity_id=build.build_id,
            owner_scope=scope,
            private_input=None,
            private_input_factory=private_input,
            public_input={
                "resource_kind": ResourceKind.KNOWLEDGE_BASE.value,
                "resource_id": build.resource_id,
                "version_id": build.version_id,
            },
            workflow={
                "build_id": build.build_id,
                "resource_id": build.resource_id,
                "version_id": build.version_id,
                "active_version_id": build.parent_version_id,
            },
            idempotency_key=f"resource-build:{build.build_id}",
            command_sink=command_sink,
        )

    async def _freeze_embedding(
        self,
        build: ResourceBuildIntent,
        scope: OwnerScope,
        *,
        policy: ExecutionPolicy,
        uow: IUnitOfWork,
    ) -> ResourceBuildIntent:
        if not policy.knowledge_base.vector_enabled:
            return build
        if self._inference_bindings is None:
            raise RuntimeError("knowledge-base vector indexing requires InferenceBindingService")
        # Resolve the effective embedding binding on the caller's UoW connection
        # rather than opening a nested UoW (P1-2). We only need the frozen model
        # id + platform dimensions here; endpoint invokability is re-validated by
        # the KB_INGEST embedding activity at execution time.
        binding = await uow.inference_binding.get_effective_binding(
            InferencePurpose.EMBEDDING,
            scope,
        )
        if binding is None:
            raise ConflictError(
                "推理用途尚未配置模型绑定",
                error_key="inference.errors.bindingNotConfigured",
                error_params={"purpose": InferencePurpose.EMBEDDING.value},
            )
        model = await uow.inference_model.get_by_id(binding.model_id, scope=scope)
        if model is None:
            raise ConflictError(
                "推理绑定引用的模型不存在或不可访问",
                error_key="inference.errors.bindingModelUnavailable",
                error_params={"purpose": InferencePurpose.EMBEDDING.value},
            )
        if not isinstance(model.settings, EmbeddingModelSettings):
            raise ConflictError("embedding binding does not reference an embedding model")
        return build.model_copy(
            update={
                "embedding_model_id": model.id,
                "embedding_dimensions": PLATFORM_EMBEDDING_DIMENSIONS,
            }
        )

    async def _enqueue_candidate(
        self,
        uow: IUnitOfWork,
        result: CandidateBuildResult,
        *,
        scope: OwnerScope,
    ) -> None:
        await self._admit_build(
            result,
            scope,
            uow=uow,
            command_sink=uow.execution_commands,
        )

    @staticmethod
    def _infer_file_source_type(filename: str, mime: str, fallback: KBSourceType) -> KBSourceType:
        lower = (filename or "").lower()
        if lower.endswith(".zip"):
            return KBSourceType.ZIP
        if fallback == KBSourceType.ZIP and not lower.endswith(".zip"):
            return KBSourceType.UPLOAD
        return fallback

    async def _fetch_url_document(
        self,
        source_type: KBSourceType,
        url: str,
    ) -> WebDocument:
        try:
            return await self._web_documents.fetch(source_type, url)
        except (OSError, RuntimeError, ValueError) as exc:
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
                document = await uow.knowledge_base.get_document(entry.document_id)
                if document is None or document.kb_id != kb.id:
                    raise NotFoundError("document not found in knowledge base manifest")
                documents.append(document)
        sources: list[KnowledgeBuildSource] = []
        for entry, document in zip(manifest, documents, strict=False):
            revision: KnowledgeDocumentRevision | None = revisions.get(entry.document_revision_id)
            if revision is None:
                raise ConflictError("knowledge manifest revision closure is incomplete")
            digest = revision.source_digest
            source_ref = revision.source_ref or document.source_ref
            source_type = document.source_type
            source_title = document.title
            source_mime = document.mime
            source_file_id = _file_id_from_source_ref(source_ref) or document.file_id
            if source_file_id:
                async with self._uow_factory() as uow:
                    file_info = await uow.file.get_by_id(
                        source_file_id,
                        scope=scope,
                    )
                if file_info is None:
                    raise BadRequestError(f"文件[{source_file_id}]不存在或无权访问")
                try:
                    stream, stored_file = await self._file_storage.download_file(source_file_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise BadRequestError(f"文件[{source_file_id}]不存在或无法下载: {exc}") from exc
                if stored_file.id != file_info.id:
                    raise BadRequestError(f"文件[{source_file_id}]不存在或无法下载")
                digest = _sha256_stream(stream)
                identity = f"file:{source_file_id}"
                source_title = file_info.filename
                source_mime = file_info.mime_type
                source_type = self._infer_file_source_type(
                    file_info.filename,
                    file_info.mime_type,
                    (
                        document.source_type
                        if document.source_type in {KBSourceType.UPLOAD, KBSourceType.ZIP}
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
                identity = f"{source_type.value}:{source_ref.strip()}"
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
        settings: dict | None = None,
        scope: OwnerScope | None = None,
    ) -> KnowledgeBase:
        kb = KnowledgeBase(
            name=name or "未命名知识库",
            settings=settings or {},
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
        )
        async with self._uow_factory() as uow:
            await uow.knowledge_base.save_kb(kb)
            await uow.commit()
        return kb

    async def add_documents(
        self,
        kb_id: str,
        *,
        file_ids: list[str] | None = None,
        urls: list[str] | None = None,
        source_type: KBSourceType = KBSourceType.UPLOAD,
        scope: OwnerScope | None = None,
    ) -> KnowledgeBase:
        file_ids = file_ids or []
        urls = urls or []
        if not file_ids and not urls:
            raise BadRequestError("请至少上传一个文件或提供一个 URL")
        if file_ids and source_type not in {
            KBSourceType.UPLOAD,
            KBSourceType.ZIP,
        }:
            raise BadRequestError("文件只能使用 upload 或 zip 来源类型")
        if urls and source_type not in {
            KBSourceType.WEB,
            KBSourceType.CONFLUENCE,
            KBSourceType.FEISHU,
        }:
            raise BadRequestError("URL 只能使用 web、confluence 或 feishu 来源类型")

        kb = await self.get_kb(kb_id, scope=scope)
        actor_id = _require_actor(scope, kb)
        sources: list[KnowledgeBuildSource] = []
        for file_id in file_ids:
            async with self._uow_factory() as uow:
                file_info = await uow.file.get_by_id(file_id, scope=scope)
            if file_info is None:
                raise BadRequestError(f"文件[{file_id}]不存在或无权访问")
            try:
                stream, stored_file = await self._file_storage.download_file(file_id)
            except (OSError, RuntimeError, ValueError) as exc:
                raise BadRequestError(f"文件[{file_id}]不存在或无法下载: {exc}") from exc
            if stored_file.id != file_info.id:
                raise BadRequestError(f"文件[{file_id}]不存在或无法下载")
            inferred = self._infer_file_source_type(
                file_info.filename, file_info.mime_type, source_type
            )
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
        normalized_scope = _require_scope(scope, kb)
        result = await self._version_builder.create_candidate(
            KnowledgeBuildCommand.add(
                kb_id,
                sources,
                actor_id=actor_id,
            ),
            scope=normalized_scope,
            before_commit=partial(
                self._enqueue_candidate,
                scope=normalized_scope,
            ),
        )
        return result.resource

    async def get_kb(self, kb_id: str, scope: OwnerScope | None = None) -> KnowledgeBase:
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
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        scope: OwnerScope | None = None,
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
                raise BadRequestError("knowledge base version is not published")
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
            entities, next_key = await uow.knowledge_base.list_entities_page_for_version(
                kb_id,
                version_id,
                q=normalized_query or None,
                after=after,
                limit=bounded_limit,
            )
            relations = await uow.knowledge_base.list_relations_for_entities_for_version(
                kb_id,
                version_id,
                [entity.id for entity in entities],
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
                    await uow.knowledge_base.get_entities_by_ids_for_version(
                        kb_id,
                        version_id,
                        missing,
                    )
                )
            entity_by_id = {entity.id: entity for entity in entities}
            safe_relations = [
                relation
                for relation in relations
                if relation.src_entity_id in entity_by_id and relation.dst_entity_id in entity_by_id
            ]
            evidence_rows = await uow.knowledge_base.get_chunks_by_ids_for_version(
                kb_id,
                version_id,
                sorted({relation.chunk_id for relation in safe_relations if relation.chunk_id}),
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

    async def list_kbs(
        self, limit: int = 100, offset: int = 0, scope: OwnerScope | None = None
    ) -> list[KnowledgeBase]:
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
        scope: OwnerScope | None = None,
    ) -> tuple[list[KnowledgeDocument], int]:
        await self.get_kb(kb_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.knowledge_base.list_documents_page(kb_id, limit=limit, offset=offset)

    async def _create_reindex_candidate(
        self,
        kb_id: str,
        *,
        scope: OwnerScope | None,
    ) -> CandidateBuildResult:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        sources = await self._prepare_reindex_sources(
            kb,
            scope=normalized_scope,
        )
        return await self._version_builder.create_candidate(
            KnowledgeBuildCommand.reindex(
                kb_id,
                sources,
                actor_id=_require_actor(normalized_scope, kb),
            ),
            scope=normalized_scope,
            before_commit=partial(
                self._enqueue_candidate,
                scope=normalized_scope,
            ),
        )

    async def list_versions(
        self,
        kb_id: str,
        *,
        scope: OwnerScope | None = None,
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
                [await self._project_version(uow, kb, version) for version in versions]
            )
            active_projection = next(
                (
                    version.build
                    for version in projected
                    if version.state is KnowledgeVersionState.BUILDING
                ),
                None,
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
        scope: OwnerScope | None = None,
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
                raise NotFoundError("knowledge base version not found in owner scope")
            return await self._project_version(uow, kb, version)

    async def create_build(
        self,
        kb_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> KnowledgeVersionProjection:
        result = await self._create_reindex_candidate(
            kb_id,
            scope=scope,
        )
        return await self._project_candidate_result(
            result,
            _require_scope(scope, result.resource),
        )

    async def retry_build(
        self,
        kb_id: str,
        build_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> KnowledgeVersionProjection:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        result = await self._version_builder.retry_candidate(
            kb.id,
            build_id,
            actor_id=_require_actor(normalized_scope, kb),
            scope=normalized_scope,
            before_commit=partial(
                self._enqueue_candidate,
                scope=normalized_scope,
            ),
        )
        return await self._project_candidate_result(result, normalized_scope)

    async def cancel_build(
        self,
        kb_id: str,
        build_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> KnowledgeBuildProjection:
        if scope is None:
            raise BadRequestError("knowledge builds require owner scope")
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id, scope=scope)
            if kb is None:
                raise NotFoundError("knowledge build not found in owner scope")
            candidate_result = await uow.knowledge_version.get_build_candidate(build_id)
            if candidate_result is None:
                raise NotFoundError("knowledge build not found in owner scope")
            candidate, _ = candidate_result
            if candidate.knowledge_base_id != kb.id:
                raise NotFoundError("knowledge build not found in owner scope")
            if candidate.state is not KnowledgeVersionState.BUILDING:
                raise ConflictError("only an active knowledge build can be cancelled")
        view = await self._resource_build_view(build_id, scope)
        projection = self._knowledge_build_projection(
            view,
            fallback=ResourceBuildIntent(
                build_id=candidate.build_id,
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=kb.id,
                version_id=candidate.id,
                parent_version_id=candidate.parent_version_id,
            ),
            created_at=candidate.created_at,
            retryable=False,
        )
        run_id = await self._run_control.cancel_source(
            source_entity_type="resource_build",
            source_entity_id=build_id,
            owner_scope=scope,
            reason="requested_by_user",
        )
        if run_id is None:
            raise ConflictError("active knowledge build has no execution Run")
        return projection

    async def _project_version(
        self,
        uow: IUnitOfWork,
        resource: KnowledgeBase,
        version: KnowledgeBaseVersion,
    ) -> KnowledgeVersionProjection:
        del uow
        is_published = (
            version.published_at is not None and version.state in _PUBLISHED_KB_VERSION_STATES
        )
        if version.id == resource.active_version_id and not is_published:
            raise ConflictError("knowledge base active version is not published")
        scope = _resource_scope(resource)
        view = await self._resource_build_view(version.build_id, scope)
        fallback_status = {
            KnowledgeVersionState.BUILDING: RunStatus.QUEUED,
            KnowledgeVersionState.READY: RunStatus.COMPLETED,
            KnowledgeVersionState.DEGRADED: RunStatus.COMPLETED,
            KnowledgeVersionState.FAILED: RunStatus.FAILED,
        }[version.state]
        build = self._knowledge_build_projection(
            view,
            fallback=ResourceBuildIntent(
                build_id=version.build_id,
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=resource.id,
                version_id=version.id,
                parent_version_id=version.parent_version_id,
            ),
            created_at=version.created_at,
            retryable=(
                version.state is KnowledgeVersionState.FAILED
                and version.parent_version_id == resource.active_version_id
            ),
            fallback_status=fallback_status,
        )
        return self._build_version_projection(
            version,
            is_active=version.id == resource.active_version_id,
            is_published=is_published,
            is_candidate=not is_published,
            build=build,
        )

    async def _resource_build_view(
        self,
        build_id: str,
        scope: OwnerScope,
    ) -> ResourceBuildView | None:
        return await self._run_projection.resource_build(
            build_id=build_id,
            owner_scope=scope,
        )

    @staticmethod
    def _knowledge_build_projection(
        view: ResourceBuildView | None,
        *,
        fallback: ResourceBuildIntent,
        created_at,
        retryable: bool,
        fallback_status: RunStatus = RunStatus.QUEUED,
    ) -> KnowledgeBuildProjection:
        status = view.status if view is not None else fallback_status
        active_statuses = {
            RunStatus.NEW,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING,
        }
        return KnowledgeBuildProjection(
            id=fallback.build_id,
            run_id=str(view.run_id) if view is not None else None,
            knowledge_base_id=fallback.resource_id,
            version_id=fallback.version_id,
            status=status,
            phase=view.phase if view is not None else None,
            progress=view.progress if view is not None else 0,
            failure_code=view.failure_code if view is not None else None,
            created_at=view.created_at if view is not None else created_at,
            updated_at=view.updated_at if view is not None else created_at,
            terminal_at=view.terminal_at if view is not None else None,
            can_retry=retryable and status in {RunStatus.FAILED, RunStatus.CANCELLED},
            can_cancel=status in active_statuses,
        )

    def _build_version_projection(
        self,
        version: KnowledgeBaseVersion,
        *,
        is_active: bool,
        is_published: bool,
        is_candidate: bool,
        build: KnowledgeBuildProjection | None,
    ) -> KnowledgeVersionProjection:
        return KnowledgeVersionProjection(
            id=version.id,
            knowledge_base_id=version.knowledge_base_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state,
            capabilities=version.capabilities,
            degraded_reasons=version.degraded_reasons,
            metrics=version.metrics,
            created_at=version.created_at,
            published_at=version.published_at,
            is_active=is_active,
            is_published=is_published,
            is_candidate=is_candidate,
            build=build,
        )

    async def _project_candidate_result(
        self,
        result: CandidateBuildResult,
        scope: OwnerScope,
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
            created_at=version.created_at,
            published_at=version.published_at,
            is_active=version.id == result.resource.active_version_id,
            is_published=False,
            is_candidate=True,
            build=self._knowledge_build_projection(
                await self._resource_build_view(result.build.build_id, scope),
                fallback=result.build,
                created_at=version.created_at,
                retryable=False,
            ),
        )

    async def stream_ingest(
        self,
        kb_id: str,
        latest_event_id: str | None = None,
        scope: OwnerScope | None = None,
    ) -> AsyncGenerator[PublicExecutionEvent, None]:
        kb = await self.get_kb(kb_id, scope=scope)
        if scope is None:
            raise BadRequestError("knowledge events require owner scope")
        async with self._uow_factory() as uow:
            candidate = await uow.knowledge_version.get_active_candidate(kb.id)
        if candidate is None:
            return
        async for event in self._run_control.stream_source(
            source_entity_type="resource_build",
            source_entity_id=candidate.build_id,
            owner_scope=scope,
            after=latest_event_id,
        ):
            yield event

    async def create_session_for_kb(
        self,
        kb_id: str,
        mode: SessionMode = SessionMode.ASK,
        model_id: str | None = None,
        skill_id: str | None = None,
        knowledge_base_version_id: str | None = None,
        scope: OwnerScope | None = None,
    ) -> Session:
        kb = await self.get_kb(kb_id, scope=scope)
        validated = None
        if self._resource_guard and scope:
            validated = await self._resource_guard.validate_session_request(
                mode=mode,
                knowledge_base_id=kb_id,
                knowledge_base_version_id=knowledge_base_version_id,
                scope=scope,
            )
        elif kb.ready_doc_count <= 0:
            raise BadRequestError("知识库尚无就绪文档，请等待索引完成后再开始问答")
        session = Session(
            title=f"文档知识库对话 · {kb.name}",
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
                    uow,
                    session_id=session.id,
                    resolved=validated.knowledge_base,
                    scope=scope,
                    actor_id=scope.user_id,
                )
                session.resource_bindings.append(binding.to_projection())
            await uow.commit()
        return session

    async def read_document_page(
        self,
        kb_id: str,
        version_id: str,
        doc_id: str,
        *,
        page: int | None = None,
        cursor: str | None = None,
        limit: int = 30,
        scope: OwnerScope | None = None,
    ) -> tuple[KnowledgeDocument, str, DocumentPage]:
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise BadRequestError("页码必须大于等于 1")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 200:
            raise BadRequestError("分页大小必须在 1 到 200 之间")
        await self.get_kb(kb_id, scope=scope)
        async with self._uow_factory() as uow:
            resolved = await uow.knowledge_base.get_document_for_version(
                kb_id,
                version_id,
                doc_id,
            )
            if resolved is None:
                raise NotFoundError(f"版本[{version_id}]中不存在可读取文档[{doc_id}]")
            document, revision_id = resolved
            try:
                source_page = await uow.knowledge_base.read_document_page_for_version(
                    kb_id,
                    version_id,
                    doc_id,
                    revision_id,
                    page_no=page,
                    cursor=cursor,
                    limit=limit,
                )
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
        return document, revision_id, source_page

    async def delete_kb(self, kb_id: str, scope: OwnerScope | None = None) -> None:
        """软删除知识库：置 ``deleted_at``，进入回收站，可恢复。

        证据链完整性要求删除可回溯，因此不再物理删除；物理删除只发生在
        ``purge_kb``（回收站手动清除或保留期到期后）。保留期清理（软删 30 天后
        自动 purge）留待调度器挂载。TODO(recycle-bin): 在 scheduled_job 服务里
        挂一个保留期清理 tick（scheduler 文件超出本次范围）。
        """
        kb = await self.get_kb(kb_id, scope=scope)
        async with self._uow_factory() as uow:
            active = await uow.knowledge_version.get_active_candidate(kb.id)
        if active is not None:
            raise ConflictError(
                "知识库正在索引中，请等待当前任务完成后再删除",
                error_key="errors.kbIndexingInProgress",
            )
        async with self._uow_factory() as uow:
            await uow.knowledge_base.soft_delete(kb_id, scope=scope)
            await uow.commit()
        logger.info("软删除知识库[%s]成功", kb_id)

    async def list_deleted_kbs(
        self,
        limit: int = 100,
        offset: int = 0,
        scope: OwnerScope | None = None,
    ) -> list[KnowledgeBase]:
        """回收站：列出当前 owner 作用域内已软删的知识库。"""
        async with self._uow_factory() as uow:
            return await uow.knowledge_base.list_deleted_kbs(
                limit=limit, offset=offset, scope=scope
            )

    async def restore_kb(self, kb_id: str, scope: OwnerScope | None = None) -> None:
        """从回收站恢复知识库：清空 ``deleted_at``。"""
        async with self._uow_factory() as uow:
            restored = await uow.knowledge_base.restore(kb_id, scope=scope)
            if not restored:
                raise NotFoundError(f"回收站中不存在知识库[{kb_id}]")
            await uow.commit()
        logger.info("恢复知识库[%s]成功", kb_id)

    async def purge_kb(self, kb_id: str, scope: OwnerScope | None = None) -> None:
        """物理清除回收站中的知识库及其级联数据（不可恢复）。"""
        async with self._uow_factory() as uow:
            purged = await uow.knowledge_base.purge_kb(kb_id, scope=scope)
            if not purged:
                raise NotFoundError(f"回收站中不存在知识库[{kb_id}]")
            await uow.commit()
        logger.info("清除知识库[%s]成功", kb_id)

    async def delete_document(
        self,
        kb_id: str,
        doc_id: str,
        scope: OwnerScope | None = None,
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
            before_commit=partial(
                self._enqueue_candidate,
                scope=normalized_scope,
            ),
        )
        return result.resource

    async def replace_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        file_id: str,
        source_type: KBSourceType = KBSourceType.UPLOAD,
        scope: OwnerScope | None = None,
    ) -> KnowledgeBase:
        kb = await self.get_kb(kb_id, scope=scope)
        normalized_scope = _require_scope(scope, kb)
        async with self._uow_factory() as uow:
            file_info = await uow.file.get_by_id(
                file_id,
                scope=normalized_scope,
            )
        if file_info is None:
            raise BadRequestError(f"文件[{file_id}]不存在或无权访问")
        try:
            stream, stored_file = await self._file_storage.download_file(file_id)
        except (OSError, RuntimeError, ValueError) as exc:
            raise BadRequestError(f"文件[{file_id}]不存在或无法下载: {exc}") from exc
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
            before_commit=partial(
                self._enqueue_candidate,
                scope=normalized_scope,
            ),
        )
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
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode())
        if (
            payload.get("kb") != kb_id
            or payload.get("version") != version_id
            or payload.get("q") != query
        ):
            raise ValueError
        name = payload["name"]
        entity_id = payload["id"]
        if not isinstance(name, str) or not name or not isinstance(entity_id, str) or not entity_id:
            raise ValueError
        return name, entity_id
    except (OSError, RuntimeError, ValueError) as exc:
        raise BadRequestError("invalid knowledge graph cursor") from exc


def _require_scope(
    scope: OwnerScope | None,
    kb: KnowledgeBase,
) -> OwnerScope:
    if scope is not None:
        return scope
    if kb.team_id:
        raise NotFoundError("knowledge base not found in owner scope")
    if kb.owner_user_id:
        return OwnerScope.personal(kb.owner_user_id)
    raise NotFoundError("knowledge base not found in owner scope")


def _resource_scope(kb: KnowledgeBase) -> OwnerScope:
    if kb.team_id:
        return OwnerScope.team(kb.owner_user_id or "projection", kb.team_id)
    if kb.owner_user_id:
        return OwnerScope.personal(kb.owner_user_id)
    raise ConflictError("knowledge base owner scope is malformed")


def _require_actor(
    scope: OwnerScope | None,
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
    with contextlib.suppress(AttributeError, OSError):
        stream.seek(0)
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        digest.update(chunk)
    with contextlib.suppress(AttributeError, OSError):
        stream.seek(0)
    return digest.hexdigest()
