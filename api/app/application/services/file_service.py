#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Tuple, BinaryIO, Callable

from fastapi import UploadFile

from app.application.errors.exceptions import NotFoundError
from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork


class FileService:
    """MyManus文件系统服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
    ) -> None:
        """构造函数，完成文件服务的初始化"""
        self.file_storage = file_storage
        self._uow_factory = uow_factory

    async def upload_file(self, upload_file: UploadFile, scope: OwnerScope | None = None) -> File:
        """将传递的文件上传到腾讯云cos并记录上传数据"""
        return await self.file_storage.upload_file(
            FileUploadPayload(
                file=upload_file.file,
                filename=upload_file.filename,
                size=upload_file.size,
                content_type=upload_file.content_type or "",
                owner_user_id=scope.user_id if scope else None,
                team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
            ),
        )

    async def get_file_info(self, file_id: str, scope: OwnerScope | None = None) -> File:
        """根据传递的文件id获取文件信息"""
        async with self._uow_factory() as uow:
            file = await uow.file.get_by_id(file_id, scope=scope)
        if not file:
            raise NotFoundError(f"该文件[{file_id}]不存在")
        return file

    async def download_file(self, file_id: str, scope: OwnerScope | None = None) -> Tuple[BinaryIO, File]:
        """根据传递的文件id下载文件"""
        if scope is None:
            return await self.file_storage.download_file(file_id)
        file = await self.get_file_info(file_id, scope=scope)
        file_data, _ = await self.file_storage.download_file(file_id)
        return file_data, file
