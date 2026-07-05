#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Materialize, analyze, index, and generate artifacts for a codebase."""
import io
import json
import logging
import os
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional, Tuple, Type

from app.domain.external.file_storage import FileStorage
from app.domain.external.sandbox import Sandbox
from app.domain.models.error_codes import EMBEDDING_UNAVAILABLE
from app.domain.models.codebase import Codebase, CodebaseSourceType, CodebaseStatus
from app.domain.models.event import BaseEvent, DoneEvent, ErrorEvent, MessageEvent, StepEvent, StepEventStatus
from app.domain.models.plan import Step
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.artifact_generator import ArtifactGenerator
from app.domain.services.codebase.indexer import CodebaseIndexer
from app.domain.services.codebase.static_analyzer import MAX_FILES, StaticAnalyzer, should_skip_path
from app.domain.utils.sandbox_result import exec_command_await, file_content, shell_output

logger = logging.getLogger(__name__)

SANDBOX_HOME = "/home/ubuntu"


def _step_event(step_id: str, description: str, status: StepEventStatus) -> StepEvent:
    return StepEvent(step=Step(id=step_id, description=description), status=status)


class CodebaseIngestionRunner:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            file_storage: FileStorage,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_cls = sandbox_cls
        self._file_storage = file_storage
        self._analyzer = StaticAnalyzer()
        self._indexer = CodebaseIndexer()

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
            artifacts = generator.generate_all(
                codebase_id,
                codebase.name,
                analysis.files,
                analysis.symbols,
                analysis.edges,
                analysis.language_stats,
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

    async def _set_status(
            self,
            codebase_id: str,
            status: CodebaseStatus,
            error: Optional[str] = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.codebase.update_status(codebase_id, status, error)

    async def _materialize(self, codebase: Codebase) -> Tuple[Sandbox, str]:
        sandbox = None
        if codebase.sandbox_id:
            sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
        if not sandbox:
            sandbox = await self._sandbox_cls.create()

        workspace = codebase.workspace_path or f"{SANDBOX_HOME}/codebase"
        await exec_command_await(
            sandbox, "ingest", SANDBOX_HOME, f"mkdir -p {workspace}", timeout=60,
        )

        if codebase.source_type == CodebaseSourceType.GIT:
            await exec_command_await(
                sandbox,
                "ingest",
                SANDBOX_HOME,
                f"rm -rf {workspace} && git clone --depth 1 {codebase.source_ref} {workspace}",
                timeout=300,
            )
        elif codebase.source_type == CodebaseSourceType.ZIP:
            refs = json.loads(codebase.source_ref) if codebase.source_ref.startswith("{") else {"file_id": codebase.source_ref}
            file_id = refs.get("file_id", codebase.source_ref)
            stream, file_info = await self._file_storage.download_file(file_id)
            data = stream.read()
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
                target = f"{workspace}/{file_info.filename}"
                parent = os.path.dirname(target)
                if parent and parent != workspace:
                    await exec_command_await(
                        sandbox, "ingest", workspace, f"mkdir -p {parent}", timeout=60,
                    )
                await sandbox.upload_file(
                    file_data=io.BytesIO(data),
                    filepath=target,
                    filename=file_info.filename,
                )

        return sandbox, workspace

    async def _collect_files(self, sandbox, workspace: str) -> List[Tuple[str, str]]:
        try:
            output = await exec_command_await(
                sandbox,
                "ingest",
                workspace,
                f"find {workspace} -type f | head -n {MAX_FILES}",
                timeout=120,
            )
        except RuntimeError as exc:
            logger.warning("列举代码库文件失败: %s", exc)
            return []
        if not output.strip():
            logger.warning("代码库工作区未发现文件: %s", workspace)
            return []
        paths = [p.strip() for p in output.splitlines() if p.strip()]
        entries: List[Tuple[str, str]] = []
        prefix = workspace.rstrip("/") + "/"
        skipped = 0
        failed = 0
        for abs_path in paths:
            rel = abs_path[len(prefix):] if abs_path.startswith(prefix) else abs_path
            if should_skip_path(rel):
                skipped += 1
                continue
            read_result = await sandbox.read_file(abs_path, max_length=512000)
            if read_result.success:
                content = file_content(read_result)
                if content:
                    entries.append((rel, content))
            else:
                failed += 1
                logger.warning(
                    "读取代码库文件失败，已跳过: path=%s error=%s",
                    rel,
                    read_result.message,
                )
        if skipped or failed:
            logger.info(
                "代码库文件采集完成: workspace=%s collected=%d skipped=%d failed=%d",
                workspace,
                len(entries),
                skipped,
                failed,
            )
        return entries
