import logging
import os.path
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import BinaryIO

from starlette.concurrency import run_in_threadpool

from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.cos import Cos

from .immutable_upload import immutable_upload

logger = logging.getLogger(__name__)


class CosFileStorage(FileStorage):
    """基于COS的文件存储扩展"""

    def __init__(
        self,
        bucket: str,
        cos: Cos,
        uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        """构造函数，完成cos文件存储桶扩展初始化"""
        self.bucket = bucket
        self.cos = cos
        self._uow_factory = uow_factory

    async def upload_file(self, payload: FileUploadPayload) -> File:
        """根据传递的文件源将文件上传到腾讯云cos"""
        try:
            with immutable_upload(payload.file) as (prepared, content_digest, verified_size):
                # 1.生成随机的uuid作为文件id并获取文件扩展名
                file_id = str(uuid.uuid4())
                _, file_extension = os.path.splitext(payload.filename)
                if not file_extension:
                    file_extension = ""

                # 2.生成日期路径并拼接最终key
                date_path = datetime.now(UTC).strftime("%Y/%m/%d")
                cos_key = f"{date_path}/{file_id}{file_extension}"

                # 3.使用fastapi的线程池来上传文件
                await run_in_threadpool(
                    self.cos.client.put_object,
                    Bucket=self.bucket,
                    Body=prepared,
                    Key=cos_key,
                )
                logger.info("文件上传成功: %s (ID: %s)", payload.filename, file_id)

                # 4.构建file模型并将数据存储到数据库中
                file = File(
                    id=file_id,
                    filename=payload.filename,
                    key=cos_key,
                    extension=file_extension,
                    mime_type=payload.content_type or "",
                    size=verified_size,
                    content_digest=content_digest,
                    object_identity=str(uuid.uuid4()),
                    owner_user_id=payload.owner_user_id,
                    team_id=payload.team_id,
                )
                async with self._uow_factory() as uow:
                    await uow.file.save(file)
                    await uow.commit()

                return file
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("上传文件[%s]失败: %s", payload.filename, e)
            raise

    async def download_file(self, file_id: str) -> tuple[BinaryIO, File]:
        """根据文件id查询数据并下载文件"""
        try:
            # 1.查询对应的文件记录是否存在
            async with self._uow_factory() as uow:
                file = await uow.file.get_by_id(file_id)
            if not file or not file.content_available:
                raise ValueError(f"该文件不存在, 文件id: {file_id}")

            # 2.全量读取 COS 对象（含 Content-Length 校验与重试）
            data = await self.cos.get_bytes(file.key)

            # 3.返回文件流+文件信息
            return BytesIO(data), file
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("下载文件[%s]失败: %s", file_id, e)
            raise

    async def delete_file(
        self, file_id: str, *, scope: OwnerScope | None = None, force: bool = False
    ) -> None:
        """根据文件id删除COS对象及数据库记录。"""
        try:
            # Durable unavailability precedes external deletion. A failed object
            # call leaves a retryable tombstone; a concurrent pin cannot revive it.
            async with self._uow_factory() as uow:
                file = await uow.file.prepare_delete(file_id, scope=scope, force=force)
                if not file:
                    raise ValueError(f"该文件不存在, 文件id: {file_id}")
                await uow.commit()
            await run_in_threadpool(self.cos.client.delete_object, Bucket=self.bucket, Key=file.key)
            async with self._uow_factory() as uow:
                await uow.file.delete(file_id, scope=scope)
                await uow.commit()
            logger.info("文件删除成功: %s (ID: %s)", file.filename, file_id)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("删除文件[%s]失败: %s", file_id, e)
            raise

    async def presigned_get_url(
        self,
        key: str,
        *,
        expires_seconds: int,
    ) -> str | None:
        return await self.cos.presigned_get_url(
            key,
            expires_seconds=expires_seconds,
        )
