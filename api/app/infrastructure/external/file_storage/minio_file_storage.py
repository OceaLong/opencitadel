#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import os.path
import uuid
from datetime import datetime
from typing import BinaryIO, Callable, Tuple

from starlette.concurrency import run_in_threadpool

from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.models.file import File
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.minio import Minio

logger = logging.getLogger(__name__)


class MinioFileStorage(FileStorage):
    """基于 MinIO 的文件存储实现。"""

    def __init__(
            self,
            bucket: str,
            minio: Minio,
            uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        self.bucket = bucket
        self.minio = minio
        self._uow_factory = uow_factory

    async def upload_file(self, payload: FileUploadPayload) -> File:
        try:
            file_id = str(uuid.uuid4())
            _, file_extension = os.path.splitext(payload.filename)
            if not file_extension:
                file_extension = ""

            date_path = datetime.now().strftime("%Y/%m/%d")
            object_key = f"{date_path}/{file_id}{file_extension}"

            length = payload.size if payload.size is not None else -1
            put_kwargs = {
                "bucket_name": self.bucket,
                "object_name": object_key,
                "data": payload.file,
                "length": length,
                "content_type": payload.content_type or "application/octet-stream",
            }
            if length == -1:
                put_kwargs["part_size"] = 10 * 1024 * 1024
            await run_in_threadpool(self.minio.client.put_object, **put_kwargs)
            logger.info("文件上传成功: %s (ID: %s)", payload.filename, file_id)

            file = File(
                id=file_id,
                filename=payload.filename,
                key=object_key,
                extension=file_extension,
                mime_type=payload.content_type or "",
                size=payload.size,
                owner_user_id=payload.owner_user_id,
                team_id=payload.team_id,
            )
            async with self._uow_factory() as uow:
                await uow.file.save(file)

            return file
        except Exception as exc:
            logger.error("上传文件[%s]失败: %s", payload.filename, exc)
            raise

    async def download_file(self, file_id: str) -> Tuple[BinaryIO, File]:
        try:
            async with self._uow_factory() as uow:
                file = await uow.file.get_by_id(file_id)
            if not file:
                raise ValueError(f"该文件不存在, 文件id: {file_id}")

            response = await run_in_threadpool(
                self.minio.client.get_object,
                self.bucket,
                file.key,
            )
            return response, file
        except Exception as exc:
            logger.error("下载文件[%s]失败: %s", file_id, exc)
            raise
