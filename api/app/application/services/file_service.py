from collections.abc import Callable
from typing import BinaryIO

from fastapi import UploadFile

from app.domain.errors import NotFoundError
from app.domain.external.file_storage import FileStorage, FileUploadPayload
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork


class FileService:
    """OpenCitadel 文件系统服务"""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        file_storage: FileStorage,
    ) -> None:
        """构造函数，完成文件服务的初始化"""
        self.file_storage = file_storage
        self._uow_factory = uow_factory

    async def upload_file(self, upload_file: UploadFile, scope: OwnerScope) -> File:
        """将传递的文件上传到对象存储并记录上传数据"""
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

    async def get_file_info(self, file_id: str, scope: OwnerScope) -> File:
        """根据传递的文件id获取文件信息"""
        async with self._uow_factory() as uow:
            file = await uow.file.get_by_id(file_id, scope=scope)
        if not file:
            raise NotFoundError(f"该文件[{file_id}]不存在")
        return file

    async def download_file(self, file_id: str, scope: OwnerScope) -> tuple[BinaryIO, File]:
        """根据传递的文件id下载文件"""
        file = await self.get_file_info(file_id, scope=scope)
        file_data, _ = await self.file_storage.download_file(file_id)
        return file_data, file

    async def delete_file(self, file_id: str, scope: OwnerScope) -> None:
        """根据传递的文件id删除文件（先校验归属，再删除对象与记录）"""
        # get_file_info 使用 scope 校验归属，非本人/本团队文件会抛 NotFoundError
        await self.get_file_info(file_id, scope=scope)
        await self.file_storage.delete_file(file_id)
