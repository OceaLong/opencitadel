#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TaskRunner wrapper for knowledge-base ingestion jobs."""
import logging
from typing import Callable, Optional

from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.task import Task, TaskRunner
from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED
from app.domain.models.event import DoneEvent, ErrorEvent
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.ingest_errors import NonRecoverableIngestError
from app.domain.services.knowledge_base.ingestion_runner import KBIngestionRunner

logger = logging.getLogger(__name__)


class KBIngestionTaskRunner(TaskRunner):
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
            kb_id: str,
            llm: Optional[LLM] = None,
            ocr_llm: Optional[LLM] = None,
            json_parser: Optional[JSONParser] = None,
    ) -> None:
        self._runner = KBIngestionRunner(
            uow_factory=uow_factory,
            file_storage=file_storage,
            llm=llm,
            ocr_llm=ocr_llm,
            json_parser=json_parser,
        )
        self._kb_id = kb_id

    async def invoke(self, task: Task) -> None:
        saw_error = False
        saw_done = False
        last_error: str | None = None
        last_error_code: str | None = None
        async for event in self._runner.run(self._kb_id):
            await task.output_stream.put(event.model_dump_json())
            if isinstance(event, ErrorEvent):
                saw_error = True
                last_error = event.error
                last_error_code = event.code
            elif isinstance(event, DoneEvent):
                saw_done = True
        if saw_error and not saw_done:
            error_code = last_error_code or DOCUMENT_PARSE_FAILED
            if error_code == DOCUMENT_PARSE_FAILED:
                raise NonRecoverableIngestError(
                    last_error or "知识库索引失败",
                    error_code=error_code,
                )
            raise RuntimeError(last_error or "知识库索引失败")

    async def on_done(self, task: Task) -> None:
        logger.info("知识库摄取任务完成: kb_id=%s task_id=%s", self._kb_id, task.id)

    async def cleanup(self) -> None:
        return

    async def destroy(self) -> None:
        return
