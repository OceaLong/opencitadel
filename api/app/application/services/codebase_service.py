#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional, Type

from app.application.errors.exceptions import NotFoundError
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
from app.domain.models.event import BaseEvent
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.ingestion_task_runner import CodebaseIngestionTaskRunner
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import get_task_state
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)


class CodebaseService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            file_storage: FileStorage,
    ) -> None:
        self._uow_factory = uow_factory
        self._sandbox_cls = sandbox_cls
        self._file_storage = file_storage
        self._task_state = get_task_state()

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
        if source_type == CodebaseSourceType.ZIP:
            source_ref = json.dumps({"file_id": file_id})
        elif source_type == CodebaseSourceType.GIT:
            source_ref = git_url or ""
        else:
            source_ref = json.dumps({"file_ids": file_ids or []})

        codebase = Codebase(
            name=name or "未命名代码库",
            source_type=source_type,
            source_ref=source_ref,
            status=CodebaseStatus.PENDING,
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
        )
        async with self._uow_factory() as uow:
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

    async def list_artifacts(
            self,
            codebase_id: str,
            kind: Optional[ArtifactKind] = None,
            scope: Optional[OwnerScope] = None,
    ) -> List[CodebaseArtifact]:
        await self.get_codebase(codebase_id, scope=scope)
        async with self._uow_factory() as uow:
            return await uow.codebase.list_artifacts(codebase_id, kind=kind)

    async def reanalyze(self, codebase_id: str, scope: Optional[OwnerScope] = None) -> Codebase:
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
            scope: Optional[OwnerScope] = None,
    ) -> Session:
        await self.get_codebase(codebase_id, scope=scope)
        session = Session(
            title=f"代码库对话",
            codebase_id=codebase_id,
            mode=mode,
            model_id=model_id,
            skill_id=skill_id,
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
        )
        async with self._uow_factory() as uow:
            await uow.session.save(session)
        return session

    async def read_source(
            self,
            codebase_id: str,
            path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            scope: Optional[OwnerScope] = None,
    ) -> str:
        codebase = await self.get_codebase(codebase_id, scope=scope)
        if not codebase.sandbox_id:
            raise NotFoundError("代码库沙箱未就绪")
        sandbox = await self._sandbox_cls.get(codebase.sandbox_id)
        if not sandbox:
            raise NotFoundError("沙箱不可用")
        full_path = f"{codebase.workspace_path.rstrip('/')}/{path.lstrip('/')}"
        result = await sandbox.read_file(full_path, start_line=start_line, end_line=end_line)
        if not result.success:
            raise NotFoundError(result.message or f"无法读取 {path}")
        return result.data or ""

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
