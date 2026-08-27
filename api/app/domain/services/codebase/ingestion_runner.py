"""Materialize, analyze, index, and generate artifacts for a codebase."""

import io
import json
import logging
import os
import re
import shlex
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.domain.external.file_storage import FileStorage
from app.domain.external.sandbox import Sandbox, SandboxFactoryPort
from app.domain.models.build_progress import (
    BuildProgress,
    BuildProgressStatus,
    build_done,
    build_error,
    build_message,
    build_step,
)
from app.domain.models.codebase import (
    ArtifactKind,
    Codebase,
    CodebaseSourceType,
    CodebaseStatus,
)
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.error_codes import EMBEDDING_UNAVAILABLE
from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import CodebaseAnalysisPolicy, CodebaseExecutionPolicy
from app.domain.services.codebase.artifact_generator import (
    ArtifactGenerationResult,
    ArtifactGenerator,
)
from app.domain.services.codebase.indexer import CodebaseIndexer
from app.domain.services.codebase.snapshot_service import CodeSnapshotService
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)
from app.domain.services.codebase.static_analyzer import (
    IGNORE_DIRS,
    IGNORE_EXTENSIONS,
    AnalysisResult,
    StaticAnalyzer,
    should_skip_path,
)
from app.domain.services.codebase.vector_service import CodebaseVectorService
from app.domain.utils.sandbox_result import exec_command_await, file_content
from app.domain.vector_port import EmbeddingPort

logger = logging.getLogger(__name__)


def _resource_scope(owner_user_id: str | None, team_id: str | None) -> OwnerScope | None:
    if team_id:
        return OwnerScope.team(owner_user_id or "resource-build", team_id)
    if owner_user_id:
        return OwnerScope.personal(owner_user_id)
    return None


SANDBOX_HOME = "/home/ubuntu"
CODEBASE_NO_INDEXABLE_SOURCE = "CODEBASE_NO_INDEXABLE_SOURCE"


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
        super().__init__(f"{CODEBASE_NO_INDEXABLE_SOURCE}: no indexable source files")
        self.result = result


def _step_event(
    step_id: str,
    description: str,
    status: BuildProgressStatus,
) -> BuildProgress:
    return build_step(step_id, description, status)


def _artifact_generation_result_parts(
    generation: ArtifactGenerationResult | list,
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
    return {kind.value: reason for kind, reason in unsupported_views.items()}


class CodebaseIngestionRunner:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        sandbox_factory: SandboxFactoryPort,
        file_storage: FileStorage,
        snapshot_service: CodeSnapshotService | None = None,
        source_validator: CodebaseSourceValidator | None = None,
        embeddings: EmbeddingPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_factory = sandbox_factory
        self._file_storage = file_storage
        self._embeddings = embeddings
        self._snapshot_service = snapshot_service or CodeSnapshotService()
        self._source_validator = source_validator or CodebaseSourceValidator()

    async def run_build(
        self,
        build_id: str,
        *,
        policy: CodebaseExecutionPolicy,
        embedding_model_id: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> AsyncGenerator[BuildProgress, None]:
        """Run one codebase candidate; lifecycle progress belongs to its Run."""
        version = None
        published_committed = False
        try:
            version, codebase = await self._load_build_context(build_id)
            if version.state is not CodebaseVersionState.BUILDING:
                yield build_done()
                return
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
            vector_service = (
                CodebaseVectorService(
                    self._embeddings,
                    scope=_resource_scope(codebase.owner_user_id, codebase.team_id),
                    enabled=policy.vector_enabled,
                    model_id=embedding_model_id,
                )
                if self._embeddings is not None
                else None
            )
            indexer = CodebaseIndexer(
                policy=policy.analysis,
                vector_service=vector_service,
            )

            yield _step_event(
                "materialize", "正在物化代码到候选版本...", BuildProgressStatus.STARTED
            )
            await self._set_status(codebase.id, CodebaseStatus.MATERIALIZING)
            sandbox, workspace = await self._materialize(
                codebase,
                build_identity=version.id,
            )
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
                await uow.commit()
            yield _step_event("materialize", "候选代码物化完成", BuildProgressStatus.COMPLETED)

            yield _step_event("analyze", "正在静态分析候选版本...", BuildProgressStatus.STARTED)
            await self._set_status(codebase.id, CodebaseStatus.ANALYZING)
            collection = await self._collect_source_files(
                sandbox,
                workspace,
                policy=policy.analysis,
            )
            if not collection.entries:
                raise CodebaseNoIndexableSourceError(collection)
            file_entries = [(entry.path, entry.content) for entry in collection.entries]

            analysis = StaticAnalyzer(policy=policy.analysis).analyze_tree(
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
                await uow.commit()

            yield _step_event(
                "analyze",
                f"候选分析完成: {len(analysis.files)} 文件, {len(analysis.symbols)} 符号",
                BuildProgressStatus.COMPLETED,
            )

            yield _step_event("index", "正在建立候选版本向量索引...", BuildProgressStatus.STARTED)
            await self._set_status(codebase.id, CodebaseStatus.INDEXING)
            vector_degraded = False
            try:
                chunks = await indexer.build_chunks(
                    codebase.id,
                    analysis.files,
                    analysis.symbols,
                    analysis.file_contents,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("候选向量索引降级（Embedding 不可用）: %s", exc)
                chunks = []
                vector_degraded = True
            if chunks and all(not c.embedding for c in chunks):
                vector_degraded = True
            chunks = [chunk.model_copy(update={"version_id": version.id}) for chunk in chunks]
            async with self._uow_factory() as uow:
                await uow.codebase.save_chunks(chunks)
                await uow.commit()

            index_desc = f"候选索引完成: {len(chunks)} 块"
            if vector_degraded:
                index_desc += "（语义检索已降级，Embedding 恢复后可重建索引）"
            yield _step_event("index", index_desc, BuildProgressStatus.COMPLETED)

            yield _step_event(
                "artifacts", "正在生成候选架构图与文档...", BuildProgressStatus.STARTED
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
            artifacts, unsupported_views = _artifact_generation_result_parts(generation)
            artifacts = [
                artifact.model_copy(update={"version_id": version.id}) for artifact in artifacts
            ]
            async with self._uow_factory() as uow:
                await uow.codebase.save_artifacts(artifacts)
                await uow.commit()

            degraded_reasons = ["EMBEDDING_UNAVAILABLE"] if vector_degraded else []
            capabilities = _artifact_capabilities(
                artifacts,
                unsupported_views,
                vector_degraded=vector_degraded,
            )
            version_state = (
                CodebaseVersionState.DEGRADED if degraded_reasons else CodebaseVersionState.READY
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
                "unsupported_views": _unsupported_view_metrics(unsupported_views),
            }

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
                current.error = ", ".join(degraded_reasons) if degraded_reasons else None
                current.updated_at = datetime.now(UTC)
                await uow.codebase.save(current)
                await uow.commit()
            published_committed = True

            yield build_message(
                message=(
                    f"代码库 **{codebase.name}** 候选版本分析完成。\n\n"
                    f"- 文件: {len(analysis.files)}\n"
                    f"- 符号: {len(analysis.symbols)}\n"
                    f"- 调用边: {len(analysis.edges)}\n"
                    f"- 索引块: {len(chunks)}\n"
                    f"- 图表: {len(artifacts)}"
                ),
            )
            yield _step_event("artifacts", "候选图表生成完成", BuildProgressStatus.COMPLETED)
            yield build_done()

        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("代码库候选构建失败")
            error_code = getattr(
                exc,
                "code",
                EMBEDDING_UNAVAILABLE if "embed" in str(exc).lower() else None,
            )
            if version is not None and not published_committed:
                await self._fail_candidate(build_id, str(exc))
            elif version is None:
                try:
                    await self._fail_candidate(build_id, str(exc))
                except (OSError, RuntimeError, ValueError):
                    logger.exception("代码库候选失败状态持久化失败 build=%s", build_id)
            yield build_error(message=str(exc), failure_code=error_code)

    async def _load_build_context(
        self,
        build_id: str,
    ):
        async with self._uow_factory() as uow:
            version = await uow.codebase_version.get_build_candidate(build_id)
            if version is None:
                raise NotFoundError(f"代码库构建不存在: {build_id}")
            codebase = await uow.codebase.get_by_id(version.codebase_id)
            if codebase is None:
                raise NotFoundError("代码库不存在")
            return version, codebase

    async def cancel(self, build_id: str) -> None:
        """Mark the unpublished artifact candidate failed after Run cancellation."""
        await self._fail_candidate(build_id, "codebase build cancelled")

    async def _fail_candidate(
        self,
        build_id: str,
        error: str,
    ) -> None:
        async with self._uow_factory() as uow:
            candidate = await uow.codebase_version.get_build_candidate(build_id)
            if candidate is None or candidate.published_at is not None:
                return
            if candidate.state is not CodebaseVersionState.FAILED:
                await uow.codebase_version.mark_failed(candidate.id, error=error)
            codebase = await uow.codebase.get_by_id(candidate.codebase_id)
            if codebase is not None and codebase.active_version_id != candidate.id:
                codebase.status = (
                    CodebaseStatus.READY
                    if codebase.active_version_id is not None
                    else CodebaseStatus.FAILED
                )
                codebase.error = error
                codebase.updated_at = datetime.now(UTC)
                await uow.codebase.save(codebase)
            await uow.commit()

    @staticmethod
    def _with_version_id(
        analysis: AnalysisResult,
        version_id: str,
    ) -> AnalysisResult:
        return AnalysisResult(
            files=[item.model_copy(update={"version_id": version_id}) for item in analysis.files],
            symbols=[
                item.model_copy(update={"version_id": version_id}) for item in analysis.symbols
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
        error: str | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.codebase.update_status(codebase_id, status, error)
            await uow.commit()

    async def _materialize(
        self,
        codebase: Codebase,
        *,
        build_identity: str,
    ) -> tuple[Sandbox, str]:
        git_url = None
        if codebase.source_type == CodebaseSourceType.GIT:
            git_url = self._validate_stored_git_url_for_clone(codebase.source_ref)

        sandbox = None
        if codebase.sandbox_id:
            sandbox = await self._sandbox_factory.get(codebase.sandbox_id)
        if not sandbox:
            owner_scope = _resource_scope(codebase.owner_user_id, codebase.team_id)
            if owner_scope is None:
                raise ConflictError("codebase owner scope is malformed")
            sandbox = await self._sandbox_factory.create(owner_scope=owner_scope)

        workspace = (
            f"{SANDBOX_HOME}/codebase-builds/"
            f"{self._safe_build_id(build_identity, fallback=codebase.id)}"
        )
        workspace_arg = shlex.quote(workspace)
        await exec_command_await(
            sandbox,
            "ingest",
            SANDBOX_HOME,
            f"rm -rf {workspace_arg} && mkdir -p {workspace_arg}",
            timeout_seconds=60,
        )

        if codebase.source_type == CodebaseSourceType.GIT:
            url_arg = shlex.quote(git_url or codebase.source_ref)
            await exec_command_await(
                sandbox,
                "ingest",
                SANDBOX_HOME,
                (f"git -c http.followRedirects=false clone --depth 1 -- {url_arg} {workspace_arg}"),
                timeout_seconds=300,
            )
        elif codebase.source_type == CodebaseSourceType.ZIP:
            refs = (
                json.loads(codebase.source_ref)
                if codebase.source_ref.startswith("{")
                else {"file_id": codebase.source_ref}
            )
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
                timeout_seconds=120,
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
                        timeout_seconds=60,
                    )
                await sandbox.upload_file(
                    file_data=io.BytesIO(data),
                    filepath=str(target),
                    filename=file_info.filename,
                )

        return sandbox, workspace

    @staticmethod
    def _safe_build_id(raw: str, *, fallback: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
        return safe or fallback

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

    async def _collect_files(
        self,
        sandbox,
        workspace: str,
        *,
        policy: CodebaseAnalysisPolicy,
    ) -> list[tuple[str, str]]:
        result = await self._collect_source_files(
            sandbox,
            workspace,
            policy=policy,
        )
        return [(entry.path, entry.content) for entry in result.entries]

    async def _collect_source_files(
        self,
        sandbox,
        workspace: str,
        *,
        policy: CodebaseAnalysisPolicy,
    ) -> SourceCollectionResult:
        try:
            output = await exec_command_await(
                sandbox,
                "ingest",
                workspace,
                self._source_traversal_command(workspace),
                timeout_seconds=120,
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
            rel = abs_path[len(prefix) :]
            if should_skip_path(rel):
                skipped += 1
                continue
            try:
                normalize_contained_path(workspace, rel)
            except BadRequestError:
                skipped += 1
                continue
            if len(eligible) >= policy.max_files:
                truncated = True
                continue
            eligible.append((abs_path, rel))

        entries: list[SourceFileEntry] = []
        failed = 0
        total_bytes = 0
        for offset in range(0, len(eligible), policy.source_read_batch_size):
            batch = eligible[offset : offset + policy.source_read_batch_size]
            results = await self._read_source_batch(
                sandbox,
                [abs_path for abs_path, _rel in batch],
                max_bytes_each=policy.max_file_size_bytes,
            )
            if len(results) < len(batch):
                failed += len(batch) - len(results)
            for (_, rel), read_result in zip(batch, results, strict=False):
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
                results.append(await sandbox.read_file(path, max_length=max_bytes_each))
            except (OSError, RuntimeError, ValueError) as exc:
                results.append(ToolResult(success=False, message=str(exc)))
        return results

    @staticmethod
    def _source_traversal_command(workspace: str) -> str:
        workspace_arg = shlex.quote(workspace)
        ignored_dirs = " -o ".join(f"-name {shlex.quote(name)}" for name in sorted(IGNORE_DIRS))
        ignored_files = " -o ".join(
            f"-name {shlex.quote('*' + suffix)}" for suffix in sorted(IGNORE_EXTENSIONS)
        )
        return (
            f"find {workspace_arg} "
            f"\\( -type d \\( {ignored_dirs} \\) -prune \\) -o "
            f"\\( -type f ! \\( {ignored_files} \\) -print \\)"
        )
