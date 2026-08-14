#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Materialize, analyze, index, and generate artifacts for a codebase."""
import io
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional, Tuple, Type
from urllib.parse import urlparse

from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.domain.external.file_storage import FileStorage
from app.domain.external.sandbox import Sandbox
from app.domain.models.error_codes import EMBEDDING_UNAVAILABLE
from app.domain.models.codebase import (
    ArtifactKind,
    Codebase,
    CodebaseSourceType,
    CodebaseStatus,
)
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, MessageEvent, StepEvent, StepEventStatus
from app.domain.models.plan import Step
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.artifact_generator import (
    ArtifactGenerationResult,
    ArtifactGenerator,
)
from app.domain.services.codebase.indexer import CodebaseIndexer
from app.domain.services.codebase.snapshot_service import CodeSnapshotService
from app.domain.services.codebase.static_analyzer import (
    AnalysisResult,
    IGNORE_DIRS,
    IGNORE_EXTENSIONS,
    MAX_FILE_SIZE,
    MAX_FILES,
    StaticAnalyzer,
    should_skip_path,
)
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)
from app.domain.utils.sandbox_result import exec_command_await, file_content, shell_output

logger = logging.getLogger(__name__)

SANDBOX_HOME = "/home/ubuntu"
CODEBASE_NO_INDEXABLE_SOURCE = "CODEBASE_NO_INDEXABLE_SOURCE"
SOURCE_READ_BATCH_SIZE = 50
_TERMINAL_BUILD_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}
_PUBLISHED_CANDIDATE_STATES = {
    CodebaseVersionState.READY,
    CodebaseVersionState.DEGRADED,
}


@dataclass(frozen=True)
class SourceFileEntry:
    path: str
    content: str


@dataclass(frozen=True)
class SourceCollectionResult:
    entries: list[SourceFileEntry]
    scanned: int = 0
    skipped: int = 0
    failed: int = 0
    truncated: bool = False
    total_bytes: int = 0


class CodebaseNoIndexableSourceError(RuntimeError):
    code = CODEBASE_NO_INDEXABLE_SOURCE

    def __init__(self, result: SourceCollectionResult) -> None:
        super().__init__(
            f"{CODEBASE_NO_INDEXABLE_SOURCE}: no indexable source files"
        )
        self.result = result


def _step_event(step_id: str, description: str, status: StepEventStatus) -> StepEvent:
    return StepEvent(step=Step(id=step_id, description=description), status=status)


def _artifact_generation_result_parts(
        generation: ArtifactGenerationResult | List,
) -> tuple[list, dict[ArtifactKind, str]]:
    if isinstance(generation, ArtifactGenerationResult):
        return generation.artifacts, generation.unsupported_views
    return list(generation), {}


def _artifact_capabilities(
        artifacts: list,
        unsupported_views: dict[ArtifactKind, str],
        *,
        vector_degraded: bool,
) -> dict[str, bool]:
    return {
        "lexical_search": True,
        "vector_search": not vector_degraded,
        "source_read": True,
        "artifact_generation": bool(artifacts),
        "architecture": ArtifactKind.ARCHITECTURE not in unsupported_views,
        "data_flow": ArtifactKind.DATA_FLOW not in unsupported_views,
        "call_chain": ArtifactKind.CALL_CHAIN not in unsupported_views,
        "flowchart": ArtifactKind.FLOWCHART not in unsupported_views,
    }


def _unsupported_view_metrics(
        unsupported_views: dict[ArtifactKind, str],
) -> dict[str, str]:
    return {
        kind.value: reason
        for kind, reason in unsupported_views.items()
    }


class CodebaseIngestionRunner:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            file_storage: FileStorage,
            snapshot_service: Optional[CodeSnapshotService] = None,
            source_validator: Optional[CodebaseSourceValidator] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_cls = sandbox_cls
        self._file_storage = file_storage
        self._analyzer = StaticAnalyzer()
        self._indexer = CodebaseIndexer()
        self._snapshot_service = snapshot_service or CodeSnapshotService()
        self._source_validator = source_validator or CodebaseSourceValidator()

    async def run(self, codebase_id: str) -> AsyncGenerator[BaseEvent, None]:
        try:
            async with self._uow_factory() as uow:
                codebase = await uow.codebase.get_by_id(codebase_id)
            if not codebase:
                yield ErrorEvent(error=f"代码库不存在: {codebase_id}", code="TASK_INFRA_FAILED")
                return

            yield _step_event("materialize", "正在物化代码到沙箱...", StepEventStatus.STARTED)
            await self._set_status(codebase_id, CodebaseStatus.MATERIALIZING)
            sandbox, workspace = await self._materialize(codebase)
            codebase.sandbox_id = sandbox.id
            codebase.workspace_path = workspace
            snapshot = await self._snapshot_service.create_from_sandbox(
                codebase.ingest_task_id or codebase.id,
                sandbox,
            )
            codebase.snapshot_key = snapshot.snapshot_key
            async with self._uow_factory() as uow:
                await uow.codebase.save(codebase)

            yield _step_event("materialize", "代码物化完成", StepEventStatus.COMPLETED)

            yield _step_event("analyze", "正在静态分析...", StepEventStatus.STARTED)
            await self._set_status(codebase_id, CodebaseStatus.ANALYZING)
            file_entries = await self._collect_files(sandbox, workspace)
            if len(file_entries) > MAX_FILES:
                file_entries = file_entries[:MAX_FILES]

            async with self._uow_factory() as uow:
                await uow.codebase.clear_analysis_data(codebase_id)

            analysis = self._analyzer.analyze_tree(codebase_id, workspace, file_entries)
            async with self._uow_factory() as uow:
                await uow.codebase.save_files(analysis.files)
                await uow.codebase.save_symbols(analysis.symbols)
                await uow.codebase.flush()
                await uow.codebase.save_edges(analysis.edges)
                codebase = await uow.codebase.get_by_id(codebase_id)
                if codebase:
                    codebase.file_count = len(analysis.files)
                    codebase.language_stats = analysis.language_stats
                    await uow.codebase.save(codebase)

            yield _step_event(
                "analyze",
                f"分析完成: {len(analysis.files)} 文件, {len(analysis.symbols)} 符号",
                StepEventStatus.COMPLETED,
            )

            yield _step_event("index", "正在建立向量索引...", StepEventStatus.STARTED)
            await self._set_status(codebase_id, CodebaseStatus.INDEXING)
            vector_degraded = False
            try:
                chunks = await self._indexer.build_chunks(
                    codebase_id,
                    analysis.files,
                    analysis.symbols,
                    analysis.file_contents,
                )
            except Exception as exc:
                logger.warning("向量索引降级（Embedding 不可用）: %s", exc)
                chunks = []
                vector_degraded = True
            if chunks and all(not c.embedding for c in chunks):
                vector_degraded = True
            async with self._uow_factory() as uow:
                await uow.codebase.save_chunks(chunks)
                codebase = await uow.codebase.get_by_id(codebase_id)
                if codebase:
                    has_vectors = any(c.embedding for c in chunks)
                    codebase.vector_degraded = vector_degraded or not has_vectors
                    if has_vectors and not vector_degraded:
                        codebase.vector_degraded = False
                    await uow.codebase.save(codebase)

            index_desc = f"索引完成: {len(chunks)} 块"
            if vector_degraded:
                index_desc += "（语义检索已降级，Embedding 恢复后可重建索引）"
            yield _step_event("index", index_desc, StepEventStatus.COMPLETED)

            yield _step_event("artifacts", "正在生成架构图与文档...", StepEventStatus.STARTED)
            await self._set_status(codebase_id, CodebaseStatus.GENERATING)
            generator = ArtifactGenerator()
            generation = generator.generate_all(
                codebase_id,
                codebase.name,
                analysis.files,
                analysis.symbols,
                analysis.edges,
                analysis.language_stats,
            )
            artifacts, _unsupported_views = _artifact_generation_result_parts(
                generation
            )
            async with self._uow_factory() as uow:
                await uow.codebase.save_artifacts(artifacts)
                codebase = await uow.codebase.get_by_id(codebase_id)
                if codebase:
                    codebase.status = CodebaseStatus.READY
                    codebase.error = None
                    codebase.updated_at = datetime.now()
                    await uow.codebase.save(codebase)

            yield MessageEvent(
                role="assistant",
                message=(
                    f"代码库 **{codebase.name}** 分析完成。\n\n"
                    f"- 文件: {len(analysis.files)}\n"
                    f"- 符号: {len(analysis.symbols)}\n"
                    f"- 调用边: {len(analysis.edges)}\n"
                    f"- 索引块: {len(chunks)}\n"
                    f"- 图表: {len(artifacts)}"
                ),
            )
            yield _step_event("artifacts", "图表生成完成", StepEventStatus.COMPLETED)
            yield DoneEvent()

        except Exception as exc:
            logger.exception("代码库摄取失败: %s", exc)
            await self._set_status(codebase_id, CodebaseStatus.FAILED, str(exc))
            yield ErrorEvent(error=str(exc), code=EMBEDDING_UNAVAILABLE if "embed" in str(exc).lower() else None)
            raise

    async def run_build(self, build_id: str) -> AsyncGenerator[BaseEvent, None]:
        """Run one durable codebase candidate build by ResourceBuild id."""
        build: ResourceBuild | None = None
        published_committed = False
        try:
            build, version, codebase = await self._load_build_context(build_id)
            if build.state in _TERMINAL_BUILD_STATES:
                yield DoneEvent()
                return

            await self._append_build_event(
                build,
                phase="materialize",
                state=BuildState.RUNNING,
                progress=0.05,
            )
            yield _step_event("materialize", "正在物化代码到候选版本...", StepEventStatus.STARTED)
            await self._set_status(codebase.id, CodebaseStatus.MATERIALIZING)
            sandbox, workspace = await self._materialize(codebase)
            codebase.status = CodebaseStatus.MATERIALIZING
            codebase.sandbox_id = sandbox.id
            codebase.workspace_path = workspace
            snapshot = await self._snapshot_service.create_from_sandbox(
                version.id,
                sandbox,
            )
            codebase.snapshot_key = snapshot.snapshot_key
            async with self._uow_factory() as uow:
                await uow.codebase.save(codebase)
                await uow.codebase_version.update_snapshot(
                    version.id,
                    source_snapshot_key=snapshot.snapshot_key,
                    source_revision=snapshot.source_revision,
                    source_digest=snapshot.source_digest,
                )
            yield _step_event("materialize", "候选代码物化完成", StepEventStatus.COMPLETED)

            await self._append_build_event(
                build,
                phase="snapshot",
                state=BuildState.RUNNING,
                progress=0.15,
                payload={
                    "source_snapshot_key": snapshot.snapshot_key,
                    "source_digest": snapshot.source_digest,
                },
            )

            yield _step_event("analyze", "正在静态分析候选版本...", StepEventStatus.STARTED)
            await self._append_build_event(
                build,
                phase="analyze",
                state=BuildState.RUNNING,
                progress=0.25,
            )
            await self._set_status(codebase.id, CodebaseStatus.ANALYZING)
            collection = await self._collect_source_files(sandbox, workspace)
            if not collection.entries:
                raise CodebaseNoIndexableSourceError(collection)
            file_entries = [
                (entry.path, entry.content)
                for entry in collection.entries
            ]

            analysis = self._analyzer.analyze_tree(
                codebase.id,
                workspace,
                file_entries,
            )
            analysis = self._with_version_id(analysis, version.id)
            async with self._uow_factory() as uow:
                await uow.codebase.save_files(analysis.files)
                await uow.codebase.save_symbols(analysis.symbols)
                await uow.codebase.flush()
                await uow.codebase.save_edges(analysis.edges)

            yield _step_event(
                "analyze",
                f"候选分析完成: {len(analysis.files)} 文件, {len(analysis.symbols)} 符号",
                StepEventStatus.COMPLETED,
            )

            yield _step_event("index", "正在建立候选版本向量索引...", StepEventStatus.STARTED)
            await self._append_build_event(
                build,
                phase="vector_index",
                state=BuildState.RUNNING,
                progress=0.55,
            )
            await self._set_status(codebase.id, CodebaseStatus.INDEXING)
            vector_degraded = False
            try:
                chunks = await self._indexer.build_chunks(
                    codebase.id,
                    analysis.files,
                    analysis.symbols,
                    analysis.file_contents,
                )
            except Exception as exc:
                logger.warning("候选向量索引降级（Embedding 不可用）: %s", exc)
                chunks = []
                vector_degraded = True
            if chunks and all(not c.embedding for c in chunks):
                vector_degraded = True
            chunks = [
                chunk.model_copy(update={"version_id": version.id})
                for chunk in chunks
            ]
            async with self._uow_factory() as uow:
                await uow.codebase.save_chunks(chunks)

            index_desc = f"候选索引完成: {len(chunks)} 块"
            if vector_degraded:
                index_desc += "（语义检索已降级，Embedding 恢复后可重建索引）"
            yield _step_event("index", index_desc, StepEventStatus.COMPLETED)

            yield _step_event("artifacts", "正在生成候选架构图与文档...", StepEventStatus.STARTED)
            await self._append_build_event(
                build,
                phase="artifacts",
                state=BuildState.RUNNING,
                progress=0.75,
            )
            await self._set_status(codebase.id, CodebaseStatus.GENERATING)
            generator = ArtifactGenerator()
            generation = generator.generate_all(
                codebase.id,
                codebase.name,
                analysis.files,
                analysis.symbols,
                analysis.edges,
                analysis.language_stats,
            )
            artifacts, unsupported_views = _artifact_generation_result_parts(
                generation
            )
            artifacts = [
                artifact.model_copy(update={"version_id": version.id})
                for artifact in artifacts
            ]
            async with self._uow_factory() as uow:
                await uow.codebase.save_artifacts(artifacts)

            degraded_reasons = (
                ["EMBEDDING_UNAVAILABLE"] if vector_degraded else []
            )
            capabilities = _artifact_capabilities(
                artifacts,
                unsupported_views,
                vector_degraded=vector_degraded,
            )
            version_state = (
                CodebaseVersionState.DEGRADED
                if degraded_reasons
                else CodebaseVersionState.READY
            )
            terminal_state = (
                BuildState.DEGRADED
                if degraded_reasons
                else BuildState.SUCCEEDED
            )
            metrics = {
                "file_count": len(analysis.files),
                "symbol_count": len(analysis.symbols),
                "edge_count": len(analysis.edges),
                "chunk_count": len(chunks),
                "artifact_count": len(artifacts),
                "source_scanned_count": collection.scanned,
                "source_skipped_count": collection.skipped,
                "source_failed_count": collection.failed,
                "source_truncated": collection.truncated,
                "source_total_bytes": collection.total_bytes,
                "unsupported_views": _unsupported_view_metrics(
                    unsupported_views
                ),
            }

            await self._append_build_event(
                build,
                phase="validate",
                state=BuildState.RUNNING,
                progress=0.9,
                payload={"metrics": metrics},
            )
            async with self._uow_factory() as uow:
                published = await uow.codebase_version.publish_candidate(
                    version.id,
                    expected_active_version_id=version.parent_version_id,
                    state=version_state,
                    capabilities=capabilities,
                    degraded_reasons=degraded_reasons,
                    metrics=metrics,
                )
                if not published:
                    raise ConflictError("codebase candidate lost active-version CAS")
                current = await uow.codebase.get_by_id(codebase.id)
                if current is None:
                    raise RuntimeError("published codebase disappeared")
                current.status = CodebaseStatus.READY
                current.file_count = len(analysis.files)
                current.language_stats = analysis.language_stats
                current.sandbox_id = sandbox.id
                current.workspace_path = workspace
                current.snapshot_key = snapshot.snapshot_key
                current.vector_degraded = vector_degraded
                if current.ingest_task_id == build.id:
                    current.ingest_task_id = None
                current.error = (
                    ", ".join(degraded_reasons)
                    if degraded_reasons
                    else None
                )
                current.updated_at = datetime.now()
                await uow.codebase.save(current)
            published_committed = True

            await self._append_build_event(
                build,
                phase="publish",
                state=terminal_state,
                progress=1.0,
                payload={
                    "capabilities": capabilities,
                    "degraded_reasons": degraded_reasons,
                    "metrics": metrics,
                },
            )

            yield MessageEvent(
                role="assistant",
                message=(
                    f"代码库 **{codebase.name}** 候选版本分析完成。\n\n"
                    f"- 文件: {len(analysis.files)}\n"
                    f"- 符号: {len(analysis.symbols)}\n"
                    f"- 调用边: {len(analysis.edges)}\n"
                    f"- 索引块: {len(chunks)}\n"
                    f"- 图表: {len(artifacts)}"
                ),
            )
            yield _step_event("artifacts", "候选图表生成完成", StepEventStatus.COMPLETED)
            yield DoneEvent()

        except Exception as exc:
            logger.exception("代码库候选构建失败: %s", exc)
            error_code = getattr(
                exc,
                "code",
                EMBEDDING_UNAVAILABLE
                if "embed" in str(exc).lower()
                else None,
            )
            if build is not None and not published_committed:
                await self._fail_build(
                    build,
                    str(exc),
                    error_code=error_code or "CODEBASE_BUILD_FAILED",
                )
            elif build is None:
                try:
                    build, _version, _codebase = await self._load_build_context(
                        build_id
                    )
                    await self._fail_build(
                        build,
                        str(exc),
                        error_code=error_code or "CODEBASE_BUILD_FAILED",
                    )
                except Exception:
                    logger.exception("代码库候选失败状态持久化失败 build=%s", build_id)
            yield ErrorEvent(error=str(exc), code=error_code)
            raise

    async def _load_build_context(
            self,
            build_id: str,
    ):
        async with self._uow_factory() as uow:
            build = await uow.resource_governance.get_build(build_id)
            if build is None:
                raise NotFoundError(f"代码库构建不存在: {build_id}")
            if build.resource_kind is not ResourceKind.CODEBASE:
                raise BadRequestError("构建类型不是代码库")
            version = await uow.codebase_version.get_version(
                build.version_id,
                codebase_id=build.resource_id,
            )
            if version is None:
                raise NotFoundError("代码库候选版本不存在")
            codebase = await uow.codebase.get_by_id(build.resource_id)
            if codebase is None:
                raise NotFoundError("代码库不存在")
            return build, version, codebase

    async def _append_build_event(
            self,
            build: ResourceBuild,
            *,
            phase: str,
            state: BuildState,
            progress: float,
            payload: Optional[dict] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            append_event = getattr(uow.resource_governance, "append_event", None)
            if append_event is None:
                return
            current = await uow.resource_governance.get_build(
                build.id,
                for_update=True,
            )
            if current is None or current.state in _TERMINAL_BUILD_STATES:
                return
            await append_event(
                build.id,
                ResourceBuildEvent(
                    build_id=build.id,
                    seq=0,
                    phase=phase,
                    state=state,
                    progress=progress,
                    payload=dict(payload or {}),
                ),
            )

    async def reconcile_stale(
            self,
            build_id: str,
            *,
            error: str = "codebase build heartbeat expired",
    ) -> BuildState | None:
        """Terminate a stale candidate build left behind by a dead worker."""
        async with self._uow_factory() as uow:
            build = await uow.resource_governance.get_build(
                build_id,
                for_update=False,
            )
        if build is None:
            return None
        if build.resource_kind is not ResourceKind.CODEBASE:
            return None
        if build.state in _TERMINAL_BUILD_STATES:
            return build.state
        return await self._fail_build(
            build,
            error,
            error_code="BUILD_STALE",
        )

    async def _fail_build(
            self,
            build: ResourceBuild,
            error: str,
            *,
            error_code: str = "CODEBASE_BUILD_FAILED",
    ) -> BuildState | None:
        async with self._uow_factory() as uow:
            current_build = await uow.resource_governance.get_build(
                build.id,
                for_update=True,
            )
            if current_build is None:
                return None
            if current_build.state in _TERMINAL_BUILD_STATES:
                # Already terminalized (e.g. a racing publish/reconcile
                # already committed the only terminal event).
                return current_build.state
            candidate = await uow.codebase_version.get_version(
                current_build.version_id,
                codebase_id=current_build.resource_id,
            )
            codebase = await uow.codebase.get_by_id(current_build.resource_id)

            published = (
                candidate is not None
                and candidate.published_at is not None
                and candidate.state in _PUBLISHED_CANDIDATE_STATES
                and codebase is not None
                and codebase.active_version_id == current_build.version_id
            )
            if published:
                # Publishing is a two-phase commit: transaction A
                # (publish_candidate + codebase made ready) has already
                # committed, but transaction B (the terminal build event)
                # has not run yet. If a stale-build reconciler lands in
                # that window, it must not overwrite the successful
                # publish with FAILED -- arbitrate the authoritative
                # terminal success event instead, mirroring the KB
                # runner's published-candidate rescue path.
                terminal_state = (
                    BuildState.DEGRADED
                    if candidate.state is CodebaseVersionState.DEGRADED
                    else BuildState.SUCCEEDED
                )
                append_event = getattr(
                    uow.resource_governance, "append_event", None
                )
                if append_event is not None:
                    await append_event(
                        current_build.id,
                        ResourceBuildEvent(
                            build_id=current_build.id,
                            seq=0,
                            phase="publish",
                            state=terminal_state,
                            progress=1.0,
                            payload={
                                "capabilities": dict(candidate.capabilities),
                                "degraded_reasons": list(
                                    candidate.degraded_reasons
                                ),
                                "metrics": dict(candidate.metrics),
                                "reconciled_after_publish": True,
                            },
                        ),
                    )
                return terminal_state

            if (
                candidate is not None
                and candidate.published_at is None
                and candidate.state is not CodebaseVersionState.FAILED
            ):
                await uow.codebase_version.mark_failed(
                    current_build.version_id,
                    error=error,
                )
            if (
                codebase is not None
                and codebase.active_version_id != current_build.version_id
            ):
                codebase.status = (
                    CodebaseStatus.READY
                    if codebase.active_version_id is not None
                    else CodebaseStatus.FAILED
                )
                if codebase.ingest_task_id == current_build.id:
                    codebase.ingest_task_id = None
                codebase.error = error
                codebase.updated_at = datetime.now()
                await uow.codebase.save(codebase)
            append_event = getattr(uow.resource_governance, "append_event", None)
            if append_event is not None:
                await append_event(
                    current_build.id,
                    ResourceBuildEvent(
                        build_id=current_build.id,
                        seq=0,
                        phase=current_build.phase or "materialize",
                        state=BuildState.FAILED,
                        progress=1.0,
                        payload={
                            "error_message": error,
                            "error_code": error_code,
                        },
                    ),
                )
            return BuildState.FAILED

    @staticmethod
    def _with_version_id(
            analysis: AnalysisResult,
            version_id: str,
    ) -> AnalysisResult:
        return AnalysisResult(
            files=[
                item.model_copy(update={"version_id": version_id})
                for item in analysis.files
            ],
            symbols=[
                item.model_copy(update={"version_id": version_id})
                for item in analysis.symbols
            ],
            edges=[
                item.model_copy(
                    update={
                        "version_id": version_id,
                        "evidence": [
                            ref.model_copy(update={"version_id": version_id})
                            for ref in item.evidence
                        ],
                    }
                )
                for item in analysis.edges
            ],
            language_stats=dict(analysis.language_stats),
            file_contents=dict(analysis.file_contents),
        )

    async def _set_status(
            self,
            codebase_id: str,
            status: CodebaseStatus,
            error: Optional[str] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.codebase.update_status(codebase_id, status, error)

    async def _materialize(self, codebase: Codebase) -> Tuple[Sandbox, str]:
        git_url = None
        if codebase.source_type == CodebaseSourceType.GIT:
            git_url = self._validate_stored_git_url_for_clone(codebase.source_ref)

        sandbox = None
        if codebase.sandbox_id:
            sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
        if not sandbox:
            sandbox = await self._sandbox_cls.create()

        workspace = f"{SANDBOX_HOME}/codebase-builds/{self._safe_build_id(codebase)}"
        workspace_arg = shlex.quote(workspace)
        await exec_command_await(
            sandbox,
            "ingest",
            SANDBOX_HOME,
            f"rm -rf {workspace_arg} && mkdir -p {workspace_arg}",
            timeout=60,
        )

        if codebase.source_type == CodebaseSourceType.GIT:
            url_arg = shlex.quote(git_url or codebase.source_ref)
            await exec_command_await(
                sandbox,
                "ingest",
                SANDBOX_HOME,
                (
                    "git -c http.followRedirects=false clone --depth 1 -- "
                    f"{url_arg} {workspace_arg}"
                ),
                timeout=300,
            )
        elif codebase.source_type == CodebaseSourceType.ZIP:
            refs = json.loads(codebase.source_ref) if codebase.source_ref.startswith("{") else {"file_id": codebase.source_ref}
            file_id = refs.get("file_id", codebase.source_ref)
            stream, file_info = await self._file_storage.download_file(file_id)
            data = stream.read()
            self._source_validator.validate_zip_bytes(data)
            await sandbox.upload_file(
                file_data=io.BytesIO(data),
                filepath=f"{workspace}/upload.zip",
                filename=file_info.filename,
            )
            await exec_command_await(
                sandbox,
                "ingest",
                workspace,
                f"cd {workspace} && python3 -m zipfile -e upload.zip . && rm -f upload.zip",
                timeout=120,
            )
        else:
            refs = json.loads(codebase.source_ref)
            file_ids = refs.get("file_ids", [])
            for file_id in file_ids:
                stream, file_info = await self._file_storage.download_file(file_id)
                data = stream.read()
                target = normalize_contained_path(workspace, file_info.filename)
                parent = os.path.dirname(str(target))
                if parent and parent != workspace:
                    await exec_command_await(
                        sandbox,
                        "ingest",
                        workspace,
                        f"mkdir -p {shlex.quote(parent)}",
                        timeout=60,
                    )
                await sandbox.upload_file(
                    file_data=io.BytesIO(data),
                    filepath=str(target),
                    filename=file_info.filename,
                )

        return sandbox, workspace

    @staticmethod
    def _safe_build_id(codebase: Codebase) -> str:
        raw = codebase.ingest_task_id or codebase.id
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
        return safe or codebase.id

    @staticmethod
    def _validate_stored_git_url_for_clone(url: str) -> str:
        if any(char in url for char in ("\r", "\n", "\t", " ", ";", "|", "&", "`", "$", "<", ">")):
            raise BadRequestError("Git URL 包含不安全字符")
        parsed = urlparse(url or "")
        if parsed.scheme != "https" or not parsed.hostname:
            raise BadRequestError("Git URL 只允许 https")
        if parsed.username or parsed.password:
            raise BadRequestError("Git URL 不能包含凭据")
        if parsed.port is not None and parsed.port != 443:
            raise BadRequestError("Git URL 不允许非默认端口")
        return url

    async def _collect_files(self, sandbox, workspace: str) -> List[Tuple[str, str]]:
        result = await self._collect_source_files(sandbox, workspace)
        return [(entry.path, entry.content) for entry in result.entries]

    async def _collect_source_files(
            self,
            sandbox,
            workspace: str,
            *,
            max_files: int = MAX_FILES,
            batch_size: int = SOURCE_READ_BATCH_SIZE,
            max_bytes_each: int = MAX_FILE_SIZE,
    ) -> SourceCollectionResult:
        try:
            output = await exec_command_await(
                sandbox,
                "ingest",
                workspace,
                self._source_traversal_command(workspace),
                timeout=120,
            )
        except RuntimeError as exc:
            logger.warning("列举代码库文件失败: %s", exc)
            return SourceCollectionResult(entries=[], failed=1)
        if not output.strip():
            logger.warning("代码库工作区未发现文件: %s", workspace)
            return SourceCollectionResult(entries=[])
        paths = [p.strip() for p in output.splitlines() if p.strip()]
        prefix = workspace.rstrip("/") + "/"
        scanned = 0
        skipped = 0
        truncated = False
        eligible: list[tuple[str, str]] = []
        for abs_path in paths:
            scanned += 1
            if not abs_path.startswith(prefix):
                skipped += 1
                continue
            rel = abs_path[len(prefix):]
            if should_skip_path(rel):
                skipped += 1
                continue
            try:
                normalize_contained_path(workspace, rel)
            except BadRequestError:
                skipped += 1
                continue
            if len(eligible) >= max_files:
                truncated = True
                continue
            eligible.append((abs_path, rel))

        entries: list[SourceFileEntry] = []
        failed = 0
        total_bytes = 0
        for offset in range(0, len(eligible), max(1, batch_size)):
            batch = eligible[offset: offset + max(1, batch_size)]
            results = await self._read_source_batch(
                sandbox,
                [abs_path for abs_path, _rel in batch],
                max_bytes_each=max_bytes_each,
            )
            if len(results) < len(batch):
                failed += len(batch) - len(results)
            for (abs_path, rel), read_result in zip(batch, results):
                if not read_result.success:
                    failed += 1
                    logger.warning(
                        "读取代码库文件失败，已跳过: path=%s error=%s",
                        rel,
                        read_result.message,
                    )
                    continue
                content = file_content(read_result)
                if not content:
                    skipped += 1
                    continue
                entries.append(SourceFileEntry(path=rel, content=content))
                total_bytes += len(content.encode("utf-8", errors="ignore"))
        if skipped or failed or truncated:
            logger.info(
                "代码库文件采集完成: workspace=%s collected=%d "
                "scanned=%d skipped=%d failed=%d truncated=%s",
                workspace,
                len(entries),
                scanned,
                skipped,
                failed,
                truncated,
            )
        return SourceCollectionResult(
            entries=entries,
            scanned=scanned,
            skipped=skipped,
            failed=failed,
            truncated=truncated,
            total_bytes=total_bytes,
        )

    async def _read_source_batch(
            self,
            sandbox,
            paths: list[str],
            *,
            max_bytes_each: int,
    ) -> list[ToolResult]:
        read_files = getattr(sandbox, "read_files", None)
        if read_files is not None:
            return await read_files(paths, max_length=max_bytes_each)
        results = []
        for path in paths:
            try:
                results.append(
                    await sandbox.read_file(path, max_length=max_bytes_each)
                )
            except Exception as exc:
                results.append(ToolResult(success=False, message=str(exc)))
        return results

    @staticmethod
    def _source_traversal_command(workspace: str) -> str:
        workspace_arg = shlex.quote(workspace)
        ignored_dirs = " -o ".join(
            f"-name {shlex.quote(name)}" for name in sorted(IGNORE_DIRS)
        )
        ignored_files = " -o ".join(
            f"-name {shlex.quote('*' + suffix)}"
            for suffix in sorted(IGNORE_EXTENSIONS)
        )
        return (
            f"find {workspace_arg} "
            f"\\( -type d \\( {ignored_dirs} \\) -prune \\) -o "
            f"\\( -type f ! \\( {ignored_files} \\) -print \\)"
        )
