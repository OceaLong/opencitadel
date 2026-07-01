#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional

from pydantic import TypeAdapter

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain.external.file_storage import FileStorage
from app.domain.models.event import BaseEvent, Event
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.knowledge_base import (
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.domain.models.session import Session
from app.domain.models.codebase import SessionMode
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.url_guard import validate_public_url
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import get_task_state

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
    ) -> None:
        self._uow_factory = uow_factory
        self._file_storage = file_storage
        self._task_state = get_task_state()

    async def _ingest_in_progress(self, kb: KnowledgeBase) -> bool:
        if not kb.ingest_task_id:
            return False
        return not await self._task_state.is_done(kb.ingest_task_id)

    @staticmethod
    def _infer_file_source_type(filename: str, mime: str, fallback: KBSourceType) -> KBSourceType:
        lower = (filename or "").lower()
        if lower.endswith(".zip"):
            return KBSourceType.ZIP
        if fallback == KBSourceType.ZIP and not lower.endswith(".zip"):
            return KBSourceType.UPLOAD
        return fallback

    async def create_kb(self, name: str = "未命名知识库", settings: Optional[dict] = None) -> KnowledgeBase:
        kb = KnowledgeBase(name=name or "未命名知识库", settings=settings or {})
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
    ) -> KnowledgeBase:
        file_ids = file_ids or []
        urls = urls or []
        if not file_ids and not urls:
            raise BadRequestError("请至少上传一个文件或提供一个 URL")

        kb = await self.get_kb(kb_id)
        if await self._ingest_in_progress(kb):
            raise ConflictError("知识库正在索引中，请等待当前任务完成后再添加文档")

        docs: list[KnowledgeDocument] = []
        for file_id in file_ids:
            try:
                _stream, file_info = await self._file_storage.download_file(file_id)
            except Exception as exc:
                raise BadRequestError(f"文件[{file_id}]不存在或无法下载: {exc}") from exc
            inferred = self._infer_file_source_type(file_info.filename, file_info.mime_type, source_type)
            docs.append(
                KnowledgeDocument(
                    kb_id=kb_id,
                    title=file_info.filename,
                    source_type=inferred,
                    source_ref=json.dumps({"file_id": file_id}, ensure_ascii=False),
                    mime=file_info.mime_type,
                    file_id=file_id,
                )
            )
        for url in urls:
            safe_url = validate_public_url(url)
            docs.append(
                KnowledgeDocument(
                    kb_id=kb_id,
                    title=safe_url,
                    source_type=source_type if source_type != KBSourceType.UPLOAD else KBSourceType.WEB,
                    source_ref=safe_url,
                    mime="text/markdown",
                )
            )
        async with self._uow_factory() as uow:
            for doc in docs:
                await uow.knowledge_base.save_document(doc)
            kb.doc_count += len(docs)
            kb.status = KBStatus.PENDING
            kb.updated_at = datetime.now()
            await uow.knowledge_base.save_kb(kb)
        return await self.reindex(kb_id)

    async def get_kb(self, kb_id: str) -> KnowledgeBase:
        async with self._uow_factory() as uow:
            kb = await uow.knowledge_base.get_kb(kb_id)
        if not kb:
            raise NotFoundError(f"知识库[{kb_id}]不存在")
        return kb

    async def list_kbs(self, limit: int = 100, offset: int = 0) -> List[KnowledgeBase]:
        async with self._uow_factory() as uow:
            return await uow.knowledge_base.list_kbs(limit=limit, offset=offset)

    async def list_documents(self, kb_id: str) -> List[KnowledgeDocument]:
        await self.get_kb(kb_id)
        async with self._uow_factory() as uow:
            return await uow.knowledge_base.list_documents(kb_id)

    async def reindex(self, kb_id: str) -> KnowledgeBase:
        kb = await self.get_kb(kb_id)
        if await self._ingest_in_progress(kb):
            logger.info("知识库 reindex 幂等返回: kb_id=%s task_id=%s", kb_id, kb.ingest_task_id)
            return kb

        task_id = str(uuid.uuid4())
        await self._task_state.register_task(
            task_id,
            session_id=f"kb-ingest:{kb_id}",
            task_type="kb_ingest",
            resource_id=kb_id,
        )
        kb.ingest_task_id = task_id
        kb.status = KBStatus.PENDING
        kb.error = None
        kb.updated_at = datetime.now()
        async with self._uow_factory() as uow:
            await uow.knowledge_base.save_kb(kb)
        task = RedisStreamTask(task_id=task_id, session_id=f"kb-ingest:{kb_id}")
        await task.dispatch_to_worker()
        return kb

    async def stream_ingest(
            self,
            kb_id: str,
            latest_event_id: Optional[str] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        kb = await self.get_kb(kb_id)
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
    ) -> Session:
        kb = await self.get_kb(kb_id)
        if kb.status != KBStatus.READY:
            raise BadRequestError("知识库尚未就绪，请等待索引完成后再开始问答")
        session = Session(
            title=f"文档知识库对话 · {kb.name}",
            knowledge_base_id=kb_id,
            mode=mode,
            model_id=model_id,
            skill_id=skill_id,
        )
        async with self._uow_factory() as uow:
            await uow.session.save(session)
        return session

    async def read_document(
            self,
            doc_id: str,
            page: Optional[int] = None,
            limit: int = 30,
            *,
            kb_id: Optional[str] = None,
    ) -> tuple[KnowledgeDocument, str]:
        async with self._uow_factory() as uow:
            doc = await uow.knowledge_base.get_document(doc_id)
            if not doc:
                raise NotFoundError(f"文档[{doc_id}]不存在")
            if kb_id and doc.kb_id != kb_id:
                raise NotFoundError(f"文档[{doc_id}]不属于知识库[{kb_id}]")
            chunks = await uow.knowledge_base.list_chunks_for_document(doc_id, page_no=page, limit=limit)
        content = "\n\n".join(chunk.content for chunk in chunks if chunk.level.value == "parent")
        return doc, content
