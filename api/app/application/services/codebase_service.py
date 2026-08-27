import hashlib
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime

from app.application.dto.codebase_build import (
    CodebaseBuildProjection,
    CodebaseVersionHistoryProjection,
    CodebaseVersionProjection,
)
from app.application.execution.admission import RunAdmissionService
from app.application.execution.public_projection import PublicExecutionEvent
from app.application.execution.run_control import RunControlService
from app.application.ports.queries import (
    ResourceBuildView,
    RunProjectionPort,
)
from app.application.services.codebase_version_service import CodebaseVersionService
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.domain.execution.run import RunFamily, RunStatus
from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.sandbox import Sandbox, SandboxFactoryPort
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
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
    InferencePurpose,
)
from app.domain.models.resource_bindings import (
    ResourceBuildIntent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import ExecutionPolicy
from app.domain.services.codebase.snapshot_service import VersionedCodeSource
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)
from app.domain.services.codebase.version_builder import CodebaseBuildPlan
from app.domain.utils.sandbox_result import file_content

logger = logging.getLogger(__name__)

_TERMINAL_CODEBASE_STATUSES = {CodebaseStatus.READY, CodebaseStatus.FAILED}
_PUBLISHED_CODEBASE_VERSION_STATES = {
    CodebaseVersionState.READY,
    CodebaseVersionState.DEGRADED,
}


class CodebaseService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        sandbox_factory: SandboxFactoryPort,
        file_storage: FileStorage,
        run_admission_service: RunAdmissionService,
        run_control_service: RunControlService,
        run_projection: RunProjectionPort,
        resource_guard: ResourceGuardService | None = None,
        resource_binding_service: ResourceBindingService | None = None,
        codebase_version_service: CodebaseVersionService | None = None,
        source_validator: CodebaseSourceValidator | None = None,
        inference_bindings: InferenceBindingService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_factory = sandbox_factory
        self._file_storage = file_storage
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service
        self._codebase_version_service = codebase_version_service
        self._source_validator = source_validator or CodebaseSourceValidator()
        self._run_admission = run_admission_service
        self._run_control = run_control_service
        self._run_projection = run_projection
        self._inference_bindings = inference_bindings

    async def create_codebase(
        self,
        name: str,
        source_type: CodebaseSourceType,
        *,
        file_id: str | None = None,
        git_url: str | None = None,
        file_ids: list[str] | None = None,
        scope: OwnerScope | None = None,
    ) -> Codebase:
        if scope is None:
            raise BadRequestError("codebase creation requires owner scope")
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
            version_id = str(uuid.uuid4())
            build = ResourceBuildIntent(
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase.id,
                version_id=version_id,
            )
            version = CodebaseVersion(
                id=version_id,
                codebase_id=codebase.id,
                build_id=build.build_id,
                request_key=hashlib.sha256(f"create:{codebase.id}".encode()).hexdigest(),
                state=CodebaseVersionState.BUILDING,
            )
            await uow.codebase_version.add_version(version)
            await self._admit_build(
                build,
                scope,
                command_sink=uow.execution_commands,
            )
            await uow.commit()
        return codebase

    async def _admit_build(
        self,
        build: ResourceBuildIntent,
        scope: OwnerScope,
        *,
        command_sink=None,
    ) -> None:
        async def private_input(policy: ExecutionPolicy):
            frozen = await self._freeze_embedding(build, scope, policy=policy)
            return {
                "build_id": frozen.build_id,
                "resource_id": frozen.resource_id,
                "version_id": frozen.version_id,
                "embedding_model_id": frozen.embedding_model_id,
                "embedding_dimensions": frozen.embedding_dimensions,
            }

        await self._run_admission.admit(
            family=RunFamily.CODEBASE_INGEST,
            source_entity_type="resource_build",
            source_entity_id=build.build_id,
            owner_scope=scope,
            private_input=None,
            private_input_factory=private_input,
            public_input={
                "resource_kind": ResourceKind.CODEBASE.value,
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
    ) -> ResourceBuildIntent:
        if not policy.codebase.vector_enabled:
            return build
        if self._inference_bindings is None:
            raise RuntimeError("codebase vector indexing requires InferenceBindingService")
        resolved = await self._inference_bindings.resolve(
            InferencePurpose.EMBEDDING,
            scope=scope,
        )
        if not isinstance(resolved.model.settings, EmbeddingModelSettings):
            raise ConflictError("embedding binding does not reference an embedding model")
        return build.model_copy(
            update={
                "embedding_model_id": resolved.id,
                "embedding_dimensions": PLATFORM_EMBEDDING_DIMENSIONS,
            }
        )

    async def list_codebases(
        self, limit: int = 100, offset: int = 0, scope: OwnerScope | None = None
    ) -> list[Codebase]:
        async with self._uow_factory() as uow:
            return await uow.codebase.list_all(limit=limit, offset=offset, scope=scope)

    async def get_codebase(self, codebase_id: str, scope: OwnerScope | None = None) -> Codebase:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
        if not codebase:
            raise NotFoundError(f"代码库[{codebase_id}]不存在")
        return codebase

    async def get_file_tree(
        self, codebase_id: str, scope: OwnerScope | None = None
    ) -> list[FileTreeNode]:
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
                    node[part] = {
                        "children": {},
                        "is_dir": is_dir,
                        "path": "/".join(parts[: i + 1]),
                        "language": f.language if not is_dir else "",
                    }
                node = node[part]["children"]

        def build_tree(d: dict) -> list[FileTreeNode]:
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

    async def list_symbols(
        self, codebase_id: str, name: str | None = None, scope: OwnerScope | None = None
    ) -> list[CodebaseSymbol]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.codebase.list_symbols(codebase_id, name=name)

    async def list_symbols_with_paths(
        self,
        codebase_id: str,
        name: str | None = None,
        scope: OwnerScope | None = None,
    ) -> list[tuple[CodebaseSymbol, str]]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            symbols = await uow.codebase.list_symbols(codebase_id, name=name)
            files = {f.id: f.path for f in await uow.codebase.list_files(codebase_id)}
        return [(symbol, files.get(symbol.file_id, "")) for symbol in symbols]

    async def list_artifacts(
        self,
        codebase_id: str,
        kind: ArtifactKind | None = None,
        scope: OwnerScope | None = None,
        codebase_version_id: str | None = None,
    ) -> list[CodebaseArtifact]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.codebase.list_artifacts(
                codebase_id,
                kind=kind,
                version_id=codebase_version_id,
            )

    async def reanalyze(self, codebase_id: str, scope: OwnerScope | None = None) -> Codebase:
        if self._codebase_version_service and scope:
            await self.create_build(codebase_id, scope=scope)
            return await self.get_codebase(codebase_id, scope=scope)

        raise BadRequestError("codebase reanalysis requires owner-scoped version builds")

    async def list_versions(
        self,
        codebase_id: str,
        *,
        scope: OwnerScope | None = None,
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
                [await self._project_version(uow, codebase, version) for version in versions]
            )
            active_projection = next(
                (
                    version.build
                    for version in projected
                    if version.state is CodebaseVersionState.BUILDING
                ),
                None,
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
        scope: OwnerScope | None = None,
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
        scope: OwnerScope | None = None,
    ) -> CodebaseVersionProjection:
        if not self._codebase_version_service or scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        codebase = await self.get_codebase(codebase_id, scope=scope)
        codebase.status = CodebaseStatus.PENDING
        codebase.error = None
        codebase.updated_at = datetime.now(UTC)

        async def enqueue(uow, candidate: CodebaseBuildPlan) -> None:
            await uow.codebase.save(codebase)
            await self._admit_build(
                candidate.build,
                scope,
                command_sink=uow.execution_commands,
            )

        plan = await self._codebase_version_service.create_reanalysis(
            codebase_id,
            actor_id=scope.user_id,
            scope=scope,
            before_commit=enqueue,
        )
        return await self._project_candidate_result(plan, codebase, scope)

    async def retry_build(
        self,
        codebase_id: str,
        build_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> CodebaseVersionProjection:
        if scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase build not found in owner scope")
            original = await uow.codebase_version.get_build_candidate(build_id)
            if original is None or original.codebase_id != codebase.id:
                raise NotFoundError("codebase build not found in owner scope")
            if original.state is not CodebaseVersionState.FAILED:
                raise ConflictError("only a failed codebase candidate can be retried")
            if original.parent_version_id != codebase.active_version_id:
                raise ConflictError("codebase build parent is not the current active version")
            candidate = original
            if candidate.published_at is not None:
                raise ConflictError("codebase build candidate closure is malformed")

            active = await uow.codebase_version.get_active_candidate(codebase.id)
            if active is not None:
                return await self._project_version(
                    uow,
                    codebase,
                    active,
                )

            retry_version_id = str(uuid.uuid4())
            retry_build = ResourceBuildIntent(
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase.id,
                version_id=retry_version_id,
                parent_version_id=codebase.active_version_id,
            )
            retry_version = CodebaseVersion(
                id=retry_version_id,
                codebase_id=codebase.id,
                parent_version_id=codebase.active_version_id,
                build_id=retry_build.build_id,
                request_key=hashlib.sha256(
                    f"reanalyze:{codebase.id}:retry:{original.build_id}".encode()
                ).hexdigest(),
                state=CodebaseVersionState.BUILDING,
            )
            retry_version = await uow.codebase_version.add_version(retry_version)
            codebase.status = CodebaseStatus.PENDING
            codebase.error = None
            codebase.updated_at = datetime.now(UTC)
            await uow.codebase.save(codebase)
            await self._admit_build(
                retry_build,
                scope,
                command_sink=uow.execution_commands,
            )
            await uow.commit()
        return await self._project_candidate_result(
            CodebaseBuildPlan(
                version=retry_version,
                build=retry_build,
                existing=False,
            ),
            codebase,
            scope,
        )

    async def cancel_build(
        self,
        codebase_id: str,
        build_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> CodebaseBuildProjection:
        if scope is None:
            raise BadRequestError("codebase version builds require owner scope")
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase build not found in owner scope")
            candidate = await uow.codebase_version.get_build_candidate(build_id)
            if candidate is None or candidate.codebase_id != codebase.id:
                raise NotFoundError("codebase build not found in owner scope")
            if candidate.state is not CodebaseVersionState.BUILDING:
                raise ConflictError("only an active codebase build can be cancelled")
        projection = self._codebase_build_projection(
            await self._resource_build_view(build_id, scope),
            fallback=ResourceBuildIntent(
                build_id=candidate.build_id,
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase.id,
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
            raise ConflictError("active codebase build has no execution Run")
        return projection

    async def _project_version(
        self,
        uow: IUnitOfWork,
        resource: Codebase,
        version: CodebaseVersion,
    ) -> CodebaseVersionProjection:
        del uow
        is_published = (
            version.published_at is not None and version.state in _PUBLISHED_CODEBASE_VERSION_STATES
        )
        if version.id == resource.active_version_id and not is_published:
            raise ConflictError("codebase active version is not published")
        view = await self._resource_build_view(
            version.build_id,
            _codebase_scope(resource),
        )
        fallback_status = {
            CodebaseVersionState.BUILDING: RunStatus.QUEUED,
            CodebaseVersionState.READY: RunStatus.COMPLETED,
            CodebaseVersionState.DEGRADED: RunStatus.COMPLETED,
            CodebaseVersionState.FAILED: RunStatus.FAILED,
        }[version.state]
        build = self._codebase_build_projection(
            view,
            fallback=ResourceBuildIntent(
                build_id=version.build_id,
                resource_kind=ResourceKind.CODEBASE,
                resource_id=resource.id,
                version_id=version.id,
                parent_version_id=version.parent_version_id,
            ),
            created_at=version.created_at,
            retryable=(
                version.state is CodebaseVersionState.FAILED
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
    def _codebase_build_projection(
        view: ResourceBuildView | None,
        *,
        fallback: ResourceBuildIntent,
        created_at,
        retryable: bool,
        fallback_status: RunStatus = RunStatus.QUEUED,
    ) -> CodebaseBuildProjection:
        status = view.status if view is not None else fallback_status
        active_statuses = {
            RunStatus.NEW,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING,
        }
        return CodebaseBuildProjection(
            id=fallback.build_id,
            run_id=str(view.run_id) if view is not None else None,
            codebase_id=fallback.resource_id,
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

    async def _project_candidate_result(
        self,
        result: CodebaseBuildPlan,
        codebase: Codebase,
        scope: OwnerScope,
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
            build=self._codebase_build_projection(
                await self._resource_build_view(result.build.build_id, scope),
                fallback=result.build,
                created_at=version.created_at,
                retryable=False,
            ),
        )

    async def stream_ingest(
        self,
        codebase_id: str,
        latest_event_id: str | None = None,
        scope: OwnerScope | None = None,
    ) -> AsyncGenerator[PublicExecutionEvent, None]:
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if scope is None:
            raise BadRequestError("codebase events require owner scope")
        async with self._uow_factory() as uow:
            candidate = await uow.codebase_version.get_active_candidate(codebase.id)
        if candidate is None:
            return
        async for event in self._run_control.stream_source(
            source_entity_type="resource_build",
            source_entity_id=candidate.build_id,
            owner_scope=scope,
            after=latest_event_id,
        ):
            yield event

    async def create_session_for_codebase(
        self,
        codebase_id: str,
        mode: SessionMode = SessionMode.ASK,
        model_id: str | None = None,
        skill_id: str | None = None,
        codebase_version_id: str | None = None,
        scope: OwnerScope | None = None,
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
            title="代码库对话",
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
                    resolved=validated.codebase,
                    scope=scope,
                    actor_id=scope.user_id,
                )
                session.resource_bindings.append(binding.to_projection())
            await uow.commit()
        return session

    async def read_source(
        self,
        codebase_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        scope: OwnerScope | None = None,
        codebase_version_id: str | None = None,
        object_storage: ObjectStoragePort | None = None,
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
        sandbox = await self._sandbox_factory.get(codebase.sandbox_id)
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
        codebase_version_id: str | None = None,
    ) -> CodebaseVersion | None:
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
        scope: OwnerScope | None = None,
    ) -> str:
        """Create tarball snapshot and store to object storage. Returns snapshot key."""
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if not codebase.sandbox_id:
            raise NotFoundError("代码库沙箱未就绪")
        sandbox = await self._sandbox_factory.get(codebase.sandbox_id)
        if not sandbox:
            raise NotFoundError("沙箱不可用")
        snapshot_bytes = await sandbox.create_workspace_snapshot(codebase_id)
        key = f"codebases/{codebase_id}/download.tgz"
        await object_storage.put_bytes(key, snapshot_bytes)
        codebase.snapshot_key = key
        codebase.updated_at = datetime.now(UTC)
        async with self._uow_factory() as uow:
            await uow.codebase.save(codebase)
            await uow.commit()
        return key

    async def attach_to_session_sandbox(
        self,
        codebase_id: str,
        session_sandbox: Sandbox,
        object_storage: ObjectStoragePort,
        scope: OwnerScope | None = None,
        codebase_version_id: str | None = None,
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

            snapshot_bytes = await object_storage.get_bytes(version.source_snapshot_key)
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

    async def delete_codebase(self, codebase_id: str, scope: OwnerScope | None = None) -> None:
        codebase = await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            active = await uow.codebase_version.get_active_candidate(codebase.id)
        if active is not None:
            raise ConflictError("代码库正在索引中，请等待当前任务完成后再删除")
        if codebase.sandbox_id:
            try:
                sandbox = await self._sandbox_factory.get(codebase.sandbox_id)
                if sandbox:
                    await sandbox.destroy()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("删除代码库时销毁 sandbox 失败 codebase=%s: %s", codebase_id, exc)
        async with self._uow_factory() as uow:
            await uow.codebase.delete_by_id(codebase_id)
            await uow.commit()
        logger.info("删除代码库[%s]成功", codebase_id)


def _codebase_scope(codebase: Codebase) -> OwnerScope:
    if codebase.team_id:
        return OwnerScope.team(
            codebase.owner_user_id or "projection",
            codebase.team_id,
        )
    if codebase.owner_user_id:
        return OwnerScope.personal(codebase.owner_user_id)
    raise ConflictError("codebase owner scope is malformed")
