#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Attachment synchronization between file storage and sandbox."""
import asyncio
import logging
from typing import BinaryIO, Callable, List, Optional

from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.external.sandbox import Sandbox
from app.domain.models.event import MessageEvent
from app.domain.models.file import File
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class AgentAttachmentSyncer:
    """Syncs user/assistant attachments between object storage and sandbox."""

    def __init__(
            self,
            session_id: str,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox: Sandbox,
            file_storage: FileStorage,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
    ) -> None:
        self._session_id = session_id
        self._uow_factory = uow_factory
        self._sandbox = sandbox
        self._file_storage = file_storage
        self._owner_user_id = owner_user_id
        self._team_id = team_id

    @staticmethod
    def get_stream_size(f: BinaryIO) -> int:
        current_pos = f.tell()
        f.seek(0, 2)
        size = f.tell()
        f.seek(current_pos)
        return size

    async def sync_file_to_sandbox(self, file_id: str) -> Optional[File]:
        try:
            file_data, file = await self._file_storage.download_file(file_id)
            filepath = f"/home/ubuntu/upload/{file.filename}"
            tool_result = await self._sandbox.upload_file(
                file_data=file_data,
                filepath=filepath,
                filename=file.filename,
            )
            if tool_result.success:
                file.filepath = filepath
                async with self._uow_factory() as uow:
                    await uow.file.save(file)
                return file
        except Exception as e:
            logger.exception("AgentAttachmentSyncer同步文件[%s]到沙箱失败: %s", file_id, e)
        return None

    async def sync_message_attachments_to_sandbox(self, event: MessageEvent) -> None:
        if not event.attachments:
            return
        try:
            results = await asyncio.gather(
                *[self.sync_file_to_sandbox(attachment.id) for attachment in event.attachments],
                return_exceptions=True,
            )
            attachments: List[File] = []
            for attachment, result in zip(event.attachments, results):
                if isinstance(result, Exception):
                    logger.exception(
                        "AgentAttachmentSyncer同步附件[%s]到沙箱失败: %s",
                        attachment.id,
                        result,
                    )
                    continue
                if result:
                    attachments.append(result)
                    async with self._uow_factory() as uow:
                        await uow.session.add_file(self._session_id, result)
            event.attachments = attachments
        except Exception as e:
            logger.exception("AgentAttachmentSyncer同步消息附件到沙箱失败: %s", e)

    async def sync_file_to_storage(self, filepath: str) -> Optional[File]:
        try:
            exists_result = await self._sandbox.check_file_exists(filepath)
            exists = (exists_result.data or {}).get("exists", False)
            if not exists_result.success or not exists:
                logger.warning(
                    "会话[%s] 跳过附件同步，沙箱文件不存在: %s",
                    self._session_id,
                    filepath,
                )
                return None

            async with self._uow_factory() as uow:
                file = await uow.session.get_file_by_path(self._session_id, filepath)

            file_data = await self._sandbox.download_file(filepath)

            if file:
                async with self._uow_factory() as uow:
                    await uow.session.remove_file(self._session_id, file.filepath)

            filename = filepath.split("/")[-1]
            upload_payload = FileUploadPayload(
                file=file_data,
                filename=filename,
                size=self.get_stream_size(file_data),
                owner_user_id=self._owner_user_id,
                team_id=self._team_id,
            )
            file = await self._file_storage.upload_file(upload_payload)
            file.filepath = filepath

            async with self._uow_factory() as uow:
                await uow.session.add_file(self._session_id, file)
            return file
        except Exception as e:
            logger.warning(
                "会话[%s] 同步文件到存储桶失败 filepath=%s error=%s",
                self._session_id,
                filepath,
                e,
            )
            return None

    async def sync_message_attachments_to_storage(self, event: MessageEvent) -> None:
        if not event.attachments:
            return
        try:
            results = await asyncio.gather(
                *[
                    self.sync_file_to_storage(attachment.filepath)
                    for attachment in event.attachments
                ],
                return_exceptions=True,
            )
            attachments: List[File] = []
            for attachment, result in zip(event.attachments, results):
                if isinstance(result, Exception):
                    logger.exception(
                        "AgentAttachmentSyncer同步附件[%s]到存储桶失败: %s",
                        attachment.filepath,
                        result,
                    )
                    continue
                if result:
                    attachments.append(result)
            event.attachments = attachments
        except Exception as e:
            logger.exception("AgentAttachmentSyncer同步消息附件到存储桶失败: %s", e)
