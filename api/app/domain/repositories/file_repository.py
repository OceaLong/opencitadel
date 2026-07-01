#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, Optional, List

from app.domain.models.file import File
from app.domain.models.scope import OwnerScope


class FileRepository(Protocol):
    """文件模型数据仓库"""

    async def save(self, file: File) -> None:
        """新增或更新文件信息"""
        ...

    async def get_by_id(self, file_id: str, scope: Optional[OwnerScope] = None) -> Optional[File]:
        """根据传递的文件id获取文件信息"""
        ...

    async def list_by_ids(self, file_ids: List[str], scope: Optional[OwnerScope] = None) -> List[File]:
        """根据传递的文件id列表批量获取文件信息"""
        ...
