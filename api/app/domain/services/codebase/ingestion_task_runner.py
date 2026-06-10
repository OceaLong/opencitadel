#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TaskRunner wrapper for codebase ingestion jobs."""
import logging
from typing import Callable, Type

from app.domain.external.file_storage import FileStorage
from app.domain.external.sandbox import Sandbox
from app.domain.external.task import TaskRunner, Task
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner

logger = logging.getLogger(__name__)


class CodebaseIngestionTaskRunner(TaskRunner):
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            file_storage: FileStorage,
            codebase_id: str,
    ) -> None:
        self._runner = CodebaseIngestionRunner(
            uow_factory=uow_factory,
            sandbox_cls=sandbox_cls,
            file_storage=file_storage,
        )
        self._codebase_id = codebase_id

    async def invoke(self, task: Task) -> None:
        async for event in self._runner.run(self._codebase_id):
            await task.output_stream.put(event.model_dump_json())

    async def on_done(self, task: Task) -> None:
        logger.info("代码库摄取任务完成: codebase_id=%s task_id=%s", self._codebase_id, task.id)

    async def cleanup(self) -> None:
        """摄取任务无额外资源需释放（沙箱由 CodebaseIngestionRunner 管理）。"""
        return

    async def destroy(self) -> None:
        """摄取任务结束时不销毁共享沙箱。"""
        return
