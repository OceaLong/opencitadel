#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional, Type

from app.application.dto.codebase_build import (
    CodebaseBuildProjection,
    CodebaseVersionHistoryProjection,
    CodebaseVersionProjection,
)
from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.application.services.codebase_version_service import CodebaseVersionService
from app.application.services.ingest_task_support import IngestTaskSupport
from app.application.services.resource_build_support import (
    ResourceBuildSupport,
    _ACTIVE_BUILD_STATES,
    _RETRYABLE_BUILD_STATES,
)
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.sandbox import Sandbox
from app.domain.models.codebase import (
    ArtifactKind,
    Codebase,
    CodebaseArtifact,
    CodebaseSourceType,
    CodebaseStatus,
    CodebaseSymbol,
    FileTreeNode,
    SessionMode,
)
from app.domain.models.codebase_version import CodebaseVersion, CodebaseVersionState
from app.domain.models.event import BaseEvent
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from app.domain.services.codebase.snapshot_service import VersionedCodeSource
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)
from app.domain.services.codebase.version_builder import CodebaseBuildPlan
from app.domain.utils.sandbox_result import file_content
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import get_task_state
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)

_TERMINAL_CODEBASE_STATUSES = {CodebaseStatus.READY, CodebaseStatus.FAILED}
# _ACTIVE_BUILD_STATES / _RETRYABLE_BUILD_STATES are shared with the knowledge
# base service and imported from resource_build_support (values unchanged).
_PUBLISHED_CODEBASE_VERSION_STATES = {
    CodebaseVersionState.READY,
    CodebaseVersionState.DEGRADED,
}


class CodebaseService(ResourceBuildSupport, IngestTaskSupport):
    # Consistency-assertion hooks consumed by ResourceBuildSupport. Error
    # strings are byte-identical to the pre-extraction literals (API contract).
    _build_resource_kind = ResourceKind.CODEBASE
    _published_version_states = _PUBLISHED_CODEBASE_VERSION_STATES

    _build_owner_closure_error = "codebase build owner closure is malformed"
    _build_candidate_closure_error = "codebase build candidate closure is malformed"
    _version_publication_error = "codebase version publication is malformed"
    _active_version_not_published_error = "codebase active version is not published"
    _version_build_missing_error = "codebase version has no matching build"
    _version_build_closure_error = "codebase version build closure is malformed"

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            file_storage: FileStorage,
            resource_guard: Optional[ResourceGuardService] = None,
            resource_binding_service: Optional[ResourceBindingService] = None,
            codebase_version_service: Optional[CodebaseVersionService] = None,
            source_validator: Optional[CodebaseSourceValidator] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_cls = sandbox_cls
        self._file_storage = file_storage
        self._task_state = get_task_state()
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service
        self._codebase_version_service = codebase_version_service
        self._source_validator = source_validator or CodebaseSourceValidator()
        self._ingest_terminal_statuses = _TERMINAL_CODEBASE_STATUSES
        self._ingest_resource_label = "代码库"
        self._ingest_session_prefix = "codebase-ingest"
        self._ingest_task_type = "codebase_ingest"

    async def create_codebase(
            self,
            name: str,
            source_type: CodebaseSourceType,
            *,
            file_id: Optional[str] = None,
            git_url: Optional[str] = None,
            file_ids: Optional[List[str]] = None,
            scope: Optional[OwnerScope] = None,
    ) -> Codebase:
        async with self._uow_factory() as uow:
            validated_source = await self._source_validator.validate_create(
                source_type,
                file_id=file_id,
                git_url=git_url,
                file_ids=file_ids,
                uow=uow,
                file_storage=self._file_storage,
                scope=scope,
            )
            codebase = Codebase(
                name=name or "未命名代码库",
                source_type=source_type,
                source_ref=validated_source.source_ref,
                status=CodebaseStatus.PENDING,
                owner_user_id=scope.user_id if scope else None,
                team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
            )
            await uow.codebase.save(codebase)

        task_id = str(uuid.uuid4())
        await self._task_state.register_task(
            task_id,
            session_id=f"codebase-ingest:{codebase.id}",
            task_type="codebase_ingest",
            resource_id=codebase.id,
        )
        codebase.ingest_task_id = task_id
        async with self._uow_factory() as uow:
            await uow.codebase.save(codebase)

        task = RedisStreamTask(task_id=task_id, session_id=f"codebase-ingest:{codebase.id}")
        await task.dispatch_to_worker()
        return codebase

    async def list_codebases(self, limit: int = 100, offset: int = 0, scope: Optional[OwnerScope] = None) -> List[Codebase]:
        async with self._uow_factory() as uow:
            return await uow.codebase.list_all(limit=limit, offset=offset, scope=scope)

    async def get_codebase(self, codebase_id: str, scope: Optional[OwnerScope] = None) -> Codebase:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
        if not codebase:
            raise NotFoundError(f"代码库[{codebase_id}]不存在")
        return codebase

    async def get_file_tree(self, codebase_id: str, scope: Optional[OwnerScope] = None) -> List[FileTreeNode]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            files = await uow.codebase.list_files(codebase_id)
        root: dict = {}
        for f in files:
            parts = f.path.split("/")
            node = root
            for i, part in enumerate(parts):
                is_dir = i < len(parts) - 1
                if part not in node:
                    node[part] = {"children": {}, "is_dir": is_dir, "path": "/".join(parts[: i + 1]), "language": f.language if not is_dir else ""}
                node = node[part]["children"]

        def build_tree(d: dict) -> List[FileTreeNode]:
            nodes = []
            for name, info in sorted(d.items()):
                nodes.append(
                    FileTreeNode(
                        name=name,
                        path=info.get("path", name),
                        is_dir=info.get("is_dir", False),
                        language=info.get("language", ""),
                        children=build_tree(info.get("children", {})),
                    )
                )
            return nodes

        return build_tree(root)

    async def list_symbols(self, codebase_id: str, name: Optional[str] = None, scope: Optional[OwnerScope] = None) -> List[CodebaseSymbol]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.codebase.list_symbols(codebase_id, name=name)

    async def list_symbols_with_paths(
            self,
            codebase_id: str,
            name: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> List[tuple[CodebaseSymbol, str]]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            symbols = await uow.codebase.list_symbols(codebase_id, name=name)
            files = {f.id: f.path for f in await uow.codebase.list_files(codebase_id)}
        return [(symbol, files.get(symbol.file_id, "")) for symbol in symbols]

    async def list_artifacts(
            self,
            codebase_id: str,
            kind: Optional[ArtifactKind] = None,
            scope: Optional[OwnerScope] = None,
            codebase_version_id: Optional[str] = None,
    ) -> List[CodebaseArtifact]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.codebase.list_artifacts(
                codebase_id,
                kind=kind,
                version_id=codebase_version_id,
            )

    async def reanalyze(self, codebase_id: str, scope: Optional[OwnerScope] = None) -> Codebase:
        if self._codebase_version_service and scope:
            await self.create_build(codebase_id, scope=scope)
            return await self.get_codebase(codebase_id, scope=scope)

        codebase = await self.get_codebase(codebase_id, scope=scope)
        codebase.status = CodebaseStatus.PENDING
        codebase.error = None
        task_id = str(uuid.uuid4())
        await self._task_state.register_task(
            task_id,
            session_id=f"codebase-ingest:{codebase_id}",
            task_type="codebase_ingest",
            resource_id=codebase_id,
        )
        codebase.ingest_task_id = task_id
        async with self._uow_factory() as uow:
            await uow.codebase.save(codebase)
        task = RedisStreamTask(task_id=task_id, session_id=f"codebase-ingest:{codebase_id}")
        await task.dispatch_to_worker()
        return codebase

    async def list_versions(
            self,
            codebase_id: str,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> CodebaseVersionHistoryProjection:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError(f"代码库[{codebase_id}]不存在")
            versions = await uow.codebase_version.list_versions(
                codebase.id,
                limit=500,
            )
            projected = tuple(
                [
                    await self._project_version(uow, codebase, version)
                    for version in versions
                ]
            )
            active_build = await uow.resource_governance.get_active_build(
                ResourceKind.CODEBASE,
                codebase.id,
            )
            active_projection = None
            if active_build is not None:
                active_projection = await self._project_build(
                    uow,
                    codebase.id,
                    active_build,
                    active_version_id=codebase.active_version_id,
                )
                if not any(
                    version.id == active_build.version_id
                    and version.build_id == active_build.id
                    for version in projected
                ):
                    raise ConflictError(
                        "active codebase build has no matching version"
                    )
        return CodebaseVersionHistoryProjection(
            codebase_id=codebase.id,
            active_version_id=codebase.active_version_id,
            active_build=active_projection,
            versions=projected,
        )

    async def get_version(
            self,
            codebase_id: str,
            version_id: str,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> CodebaseVersionProjection:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError(f"代码库[{codebase_id}]不存在")
            version = await uow.codebase_version.get_version(
                version_id,
                codebase_id=codebase.id,
            )
            if version is None:
                raise NotFoundError("codebase version not found in owner scope")
            return await self._project_version(uow, codebase, version)

    async def create_build(
            self,
            codebase_id: str,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> CodebaseVersionProjection:
        if not self._codebase_version_service or scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        codebase = await self.get_codebase(codebase_id, scope=scope)
        plan = await self._codebase_version_service.create_reanalysis(
            codebase_id,
            actor_id=scope.user_id,
            scope=scope,
        )
        task_id = plan.build.id
        if not plan.existing:
            codebase.status = CodebaseStatus.PENDING
            codebase.error = None
        codebase.ingest_task_id = task_id
        codebase.updated_at = datetime.now()
        async with self._uow_factory() as uow:
            await uow.codebase.save(codebase)
        if not plan.existing:
            await self._dispatch_codebase_build(plan.build)
        return self._project_candidate_result(plan, codebase)

    async def retry_build(
            self,
            codebase_id: str,
            build_id: str,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> CodebaseVersionProjection:
        if scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase build not found in owner scope")
            original = await uow.resource_governance.get_build(build_id)
            if (
                original is None
                or original.resource_kind is not ResourceKind.CODEBASE
                or original.resource_id != codebase.id
            ):
                raise NotFoundError("codebase build not found in owner scope")
            if original.state not in _RETRYABLE_BUILD_STATES:
                raise ConflictError(
                    "only a failed or cancelled codebase build can be retried"
                )
            if original.parent_version_id != codebase.active_version_id:
                raise ConflictError(
                    "codebase build parent is not the current active version"
                )
            candidate = await uow.codebase_version.get_version(
                original.version_id,
                codebase_id=codebase.id,
            )
            if (
                candidate is None
                or candidate.codebase_id != codebase.id
                or candidate.build_id != original.id
                or candidate.parent_version_id != original.parent_version_id
                or candidate.published_at is not None
            ):
                raise ConflictError(
                    "codebase build candidate closure is malformed"
                )

            active = await uow.resource_governance.get_active_build(
                ResourceKind.CODEBASE,
                codebase.id,
            )
            if active is not None:
                active_version = await uow.codebase_version.get_version(
                    active.version_id,
                    codebase_id=codebase.id,
                )
                if active_version is None:
                    raise ConflictError(
                        "active codebase build has no matching version"
                    )
                return await self._project_version(
                    uow,
                    codebase,
                    active_version,
                )

            retry_version_id = str(uuid.uuid4())
            retry_build = ResourceBuild(
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase.id,
                version_id=retry_version_id,
                parent_version_id=codebase.active_version_id,
                command_key=f"reanalyze:{codebase.id}:retry:{original.id}",
                state=BuildState.QUEUED,
                created_by=scope.user_id,
            )
            retry_version = CodebaseVersion(
                id=retry_version_id,
                codebase_id=codebase.id,
                parent_version_id=codebase.active_version_id,
                build_id=retry_build.id,
                state=CodebaseVersionState.BUILDING,
            )
            retry_build = await uow.resource_governance.add_build(retry_build)
            retry_version = await uow.codebase_version.add_version(
                retry_version
            )
            codebase.status = CodebaseStatus.PENDING
            codebase.ingest_task_id = retry_build.id
            codebase.error = None
            codebase.updated_at = datetime.now()
            await uow.codebase.save(codebase)
            projection = self._project_candidate_result(
                CodebaseBuildPlan(
                    version=retry_version,
                    build=retry_build,
                    existing=False,
                ),
                codebase,
            )
        await self._dispatch_codebase_build(retry_build)
        return projection

    async def cancel_build(
            self,
            codebase_id: str,
            build_id: str,
            *,
            scope: Optional[OwnerScope] = None,
    ) -> CodebaseBuildProjection:
        # Not hoisted to ResourceBuildSupport: semantically diverges from the
        # knowledge-base variant (mandatory owner-scope guard, mark_failed of
        # the candidate, ingest_task clearing and a resource_governance
        # append_event step that the KB side does not perform).
        if scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase build not found in owner scope")
            build = await uow.resource_governance.get_build(
                build_id,
                for_update=True,
            )
            if (
                build is None
                or build.resource_kind is not ResourceKind.CODEBASE
                or build.resource_id != codebase.id
            ):
                raise NotFoundError("codebase build not found in owner scope")
            if build.state not in _ACTIVE_BUILD_STATES:
                raise ConflictError(
                    "only an active codebase build can be cancelled"
                )
            candidate = await uow.codebase_version.get_version(
                build.version_id,
                codebase_id=codebase.id,
            )
            if (
                candidate is None
                or candidate.build_id != build.id
                or candidate.parent_version_id != build.parent_version_id
                or build.parent_version_id != codebase.active_version_id
                or candidate.state is not CodebaseVersionState.BUILDING
                or candidate.published_at is not None
            ):
                raise ConflictError(
                    "codebase build candidate closure is malformed"
                )
            await uow.codebase_version.mark_failed(
                candidate.id,
                error="codebase build cancelled",
            )
            if codebase.ingest_task_id == build.id:
                codebase.ingest_task_id = None
                codebase.updated_at = datetime.now()
                await uow.codebase.save(codebase)
            append_event = getattr(uow.resource_governance, "append_event", None)
            if append_event is not None:
                await append_event(
                    build.id,
                    ResourceBuildEvent(
                        build_id=build.id,
                        seq=0,
                        phase=build.phase or "materialize",
                        state=BuildState.CANCELLED,
                        progress=1.0,
                        payload={
                            "error_message": "codebase build cancelled",
                            "error_code": "CODEBASE_BUILD_CANCELLED",
                        },
                    ),
                )
                build = await uow.resource_governance.get_build(build.id)
                if build is None:
                    raise ConflictError(
                        "cancelled codebase build disappeared"
                    )
            projection = self._build_projection(
                build,
                active_version_id=codebase.active_version_id,
            )
        await self._task_state.request_cancel(build.id)
        return projection

    async def _dispatch_codebase_build(self, build: ResourceBuild) -> None:
        await self._dispatch_ingest_task(build.id, resource_id=build.resource_id)

    async def _get_projection_version(
            self,
            uow: IUnitOfWork,
            version_id: str,
            resource_id: str,
    ) -> CodebaseVersion | None:
        return await uow.codebase_version.get_version(
            version_id,
            codebase_id=resource_id,
        )

    def _build_version_projection(
            self,
            version: CodebaseVersion,
            *,
            is_active: bool,
            is_published: bool,
            is_candidate: bool,
            build: CodebaseBuildProjection | None,
    ) -> CodebaseVersionProjection:
        return CodebaseVersionProjection(
            id=version.id,
            codebase_id=version.codebase_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state,
            source_snapshot_key=version.source_snapshot_key,
            source_revision=version.source_revision,
            source_digest=version.source_digest,
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

    @staticmethod
    def _build_projection(
            build: ResourceBuild,
            *,
            active_version_id: str | None,
    ) -> CodebaseBuildProjection:
        return CodebaseBuildProjection(
            id=build.id,
            codebase_id=build.resource_id,
            version_id=build.version_id,
            parent_version_id=build.parent_version_id,
            command_key=build.command_key,
            state=build.state,
            phase=build.phase,
            progress=build.progress,
            capabilities=tuple(build.capabilities),
            degraded_reasons=tuple(build.degraded_reasons),
            metrics=build.metrics,
            error_code=build.error_code,
            error_message=build.error_message,
            heartbeat_at=build.heartbeat_at,
            last_event_seq=build.last_event_seq,
            created_at=build.created_at,
            started_at=build.started_at,
            finished_at=build.finished_at,
            can_retry=build.state in _RETRYABLE_BUILD_STATES
            and build.parent_version_id == active_version_id,
            can_cancel=build.state in _ACTIVE_BUILD_STATES,
        )

    @classmethod
    def _project_candidate_result(
            cls,
            result: CodebaseBuildPlan,
            codebase: Codebase,
    ) -> CodebaseVersionProjection:
        # Not hoisted: the signature diverges from the knowledge-base variant
        # (explicit ``codebase`` arg vs ``result.resource``) and the projected
        # DTO carries codebase-only ``source_*`` fields.
        version = result.version
        return CodebaseVersionProjection(
            id=version.id,
            codebase_id=version.codebase_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state,
            source_snapshot_key=version.source_snapshot_key,
            source_revision=version.source_revision,
            source_digest=version.source_digest,
            capabilities=version.capabilities,
            degraded_reasons=version.degraded_reasons,
            metrics=version.metrics,
            created_at=version.created_at,
            published_at=version.published_at,
            is_active=version.id == codebase.active_version_id,
            is_published=False,
            is_candidate=True,
            build=cls._build_projection(
                result.build,
                active_version_id=codebase.active_version_id,
            ),
        )

    async def stream_ingest(
            self,
            codebase_id: str,
            latest_event_id: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        import json
        from app.domain.models.event import Event
        from app.domain.models.event_upgrader import upgrade_event_payload

        codebase = await self.get_codebase(codebase_id, scope=scope)
        if not codebase.ingest_task_id:
            return
        output = RedisStreamMessageQueue(f"task:output:{codebase.ingest_task_id}")
        cursor = latest_event_id or "0"
        while True:
            if await self._task_state.is_cancelled(codebase.ingest_task_id):
                break
            event_id, event_str = await output.get(start_id=cursor, block_ms=1000)
            if event_str is not None:
                cursor = event_id
                event_payload = json.loads(event_str)
                event = TypeAdapter(Event).validate_python(upgrade_event_payload(event_payload))
                event.id = event_id
                yield event
                if event.type in {"done", "error"}:
                    return
                continue
            if await self._task_state.is_done(codebase.ingest_task_id):
                return

    async def create_session_for_codebase(
            self,
            codebase_id: str,
            mode: SessionMode = SessionMode.ASK,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            codebase_version_id: Optional[str] = None,
            scope: Optional[OwnerScope] = None,
    ) -> Session:
        await self.get_codebase(codebase_id, scope=scope)
        validated = None
        if self._resource_guard and scope:
            validated = await self._resource_guard.validate_session_request(
                mode=mode,
                codebase_id=codebase_id,
                codebase_version_id=codebase_version_id,
                knowledge_base_id=None,
                knowledge_base_version_id=None,
                scope=scope,
            )
        session = Session(
            title=f"代码库对话",
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
                    uow, session_id=session.id, resolved=validated.codebase,
                    scope=scope, actor_id=scope.user_id,
                )
                session.resource_bindings.append(binding.to_projection())
        return session

    async def read_source(
            self,
            codebase_id: str,
            path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            scope: Optional[OwnerScope] = None,
            codebase_version_id: Optional[str] = None,
            object_storage: Optional[ObjectStoragePort] = None,
    ) -> str:
        normalize_contained_path("/source", path)
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if object_storage is not None:
            version = await self._resolve_source_version(
                codebase,
                codebase_version_id=codebase_version_id,
            )
            if version is not None and version.source_snapshot_key:
                reader = VersionedCodeSource(
                    version_id=version.id,
                    snapshot_key=version.source_snapshot_key,
                    source_digest=version.source_digest,
                    object_storage=object_storage,
                )
                return (
                    await reader.read(
                        path,
                        start_line=start_line,
                        end_line=end_line,
                    )
                ).content
            if codebase_version_id is not None:
                raise NotFoundError("代码库版本快照不可用")
        if not codebase.sandbox_id:
            raise NotFoundError("代码库沙箱未就绪")
        sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
        if not sandbox:
            raise NotFoundError("沙箱不可用")
        full_path = normalize_contained_path(codebase.workspace_path, path)
        result = await sandbox.read_file(str(full_path), start_line=start_line, end_line=end_line)
        if not result.success:
            raise NotFoundError(result.message or f"无法读取 {path}")
        return file_content(result)

    async def _resolve_source_version(
            self,
            codebase: Codebase,
            *,
            codebase_version_id: Optional[str] = None,
    ) -> Optional[CodebaseVersion]:
        version_id = codebase_version_id or codebase.active_version_id
        if not version_id:
            return None
        async with self._uow_factory() as uow:
            version = await uow.codebase_version.get_version(
                version_id,
                codebase_id=codebase.id,
            )
        if version is None:
            if codebase_version_id is not None:
                raise NotFoundError("代码库版本不存在或无权访问")
            return None
        if version.published_at is None or version.state not in {
            CodebaseVersionState.READY,
            CodebaseVersionState.DEGRADED,
        }:
            raise NotFoundError("代码库版本不是可用的已发布版本")
        return version

    async def package_download(
            self,
            codebase_id: str,
            object_storage: ObjectStoragePort,
            scope: Optional[OwnerScope] = None,
    ) -> str:
        """Create tarball snapshot and store to object storage. Returns snapshot key."""
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if not codebase.sandbox_id:
            raise NotFoundError("代码库沙箱未就绪")
        sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
        if not sandbox:
            raise NotFoundError("沙箱不可用")
        snapshot_bytes = await sandbox.create_workspace_snapshot(codebase_id)
        key = f"codebases/{codebase_id}/download.tgz"
        await object_storage.put_bytes(key, snapshot_bytes)
        codebase.snapshot_key = key
        codebase.updated_at = datetime.now()
        async with self._uow_factory() as uow:
            await uow.codebase.save(codebase)
        return key

    async def attach_to_session_sandbox(
            self,
            codebase_id: str,
            session_sandbox: Sandbox,
            object_storage: ObjectStoragePort,
            scope: Optional[OwnerScope] = None,
            codebase_version_id: Optional[str] = None,
    ) -> None:
        """Restore codebase snapshot into a session sandbox for Agent code editing (idempotent)."""
        import io

        codebase = await self.get_codebase(codebase_id, scope=scope)
        version = await self._resolve_source_version(
            codebase,
            codebase_version_id=codebase_version_id,
        )
        if version is not None:
            if not version.source_snapshot_key or not version.source_digest:
                raise NotFoundError("代码库版本快照不可用")
            sentinel_path = (
                f"/home/ubuntu/.oc_codebase_attached_"
                f"{codebase_id}_{version.id}_{version.source_digest}"
            )
            exists = await session_sandbox.check_file_exists(sentinel_path)
            if exists.success and exists.data:
                logger.debug(
                    "代码库 %s 版本 %s 已附着到会话沙箱，跳过 restore",
                    codebase_id,
                    version.id,
                )
                return

            snapshot_bytes = await object_storage.get_bytes(
                version.source_snapshot_key
            )
            await session_sandbox.restore_workspace_snapshot(
                f"codebase-{codebase_id}-{version.id}",
                io.BytesIO(snapshot_bytes),
            )
            await session_sandbox.ensure_sandbox()
            await session_sandbox.write_file(
                sentinel_path,
                f"attached:{codebase_id}:{version.id}:{version.source_digest}\n",
                leading_newline=False,
                trailing_newline=False,
            )
            logger.info(
                "已将代码库 %s 版本 %s 快照物化到会话沙箱",
                codebase_id,
                version.id,
            )
            return

        sentinel_path = f"/home/ubuntu/.oc_codebase_attached_{codebase_id}"
        exists = await session_sandbox.check_file_exists(sentinel_path)
        if exists.success and exists.data:
            logger.debug("代码库 %s 已附着到会话沙箱，跳过 restore", codebase_id)
            return

        snapshot_key = codebase.snapshot_key
        if not snapshot_key:
            if not codebase.sandbox_id:
                raise NotFoundError("代码库沙箱未就绪")
            snapshot_key = await self.package_download(codebase_id, object_storage, scope=scope)
        snapshot_bytes = await object_storage.get_bytes(snapshot_key)
        await session_sandbox.restore_workspace_snapshot(
            f"codebase-{codebase_id}",
            io.BytesIO(snapshot_bytes),
        )
        await session_sandbox.ensure_sandbox()
        await session_sandbox.write_file(
            sentinel_path,
            f"attached:{codebase_id}\n",
            leading_newline=False,
            trailing_newline=False,
        )
        logger.info("已将代码库 %s 快照物化到会话沙箱", codebase_id)

    async def delete_codebase(self, codebase_id: str, scope: Optional[OwnerScope] = None) -> None:
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if await self._ingest_in_progress(codebase):
            raise ConflictError("代码库正在索引中，请等待当前任务完成后再删除")
        if codebase.ingest_task_id:
            await self._task_state.request_cancel(codebase.ingest_task_id)
            await self._wait_for_task_drain(codebase.ingest_task_id)
        if codebase.sandbox_id:
            try:
                sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
                if sandbox:
                    await sandbox.destroy()
            except Exception as exc:
                logger.warning("删除代码库时销毁 sandbox 失败 codebase=%s: %s", codebase_id, exc)
        async with self._uow_factory() as uow:
            await uow.codebase.delete_by_id(codebase_id)
        logger.info("删除代码库[%s]成功", codebase_id)
