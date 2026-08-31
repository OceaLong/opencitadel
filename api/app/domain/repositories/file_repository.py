from typing import Protocol

from app.domain.models.file import File
from app.domain.models.scope import OwnerScope


class FileRepository(Protocol):
    """文件模型数据仓库"""

    async def save(self, file: File) -> None:
        """新增或更新文件信息"""
        ...

    async def get_by_id(self, file_id: str, scope: OwnerScope | None = None) -> File | None:
        """根据传递的文件id获取文件信息"""
        ...

    async def list_by_ids(self, file_ids: list[str], scope: OwnerScope | None = None) -> list[File]:
        """根据传递的文件id列表批量获取文件信息"""
        ...

    async def delete(self, file_id: str, scope: OwnerScope | None = None) -> bool:
        """根据传递的文件id删除文件记录，返回是否删除成功"""
        ...
