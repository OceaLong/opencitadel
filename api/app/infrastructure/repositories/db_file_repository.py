#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.file import File
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.file_repository import FileRepository
from app.infrastructure.models import FileModel


class DBFileRepository(FileRepository):
    """基于数据库的文件数据仓库"""

    def __init__(self, db_session: AsyncSession) -> None:
        """构造函数，完成数据仓库初始化"""
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(FileModel.team_id == scope.team_id)
        return stmt.where(FileModel.owner_user_id == scope.user_id, FileModel.team_id.is_(None))

    async def save(self, file: File) -> None:
        """根据传递的文件模型存储or更新数据"""
        # 1.根据id查询记录是否存在
        stmt = select(FileModel).where(FileModel.id == file.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()

        # 2.判断如果文件不存在则新建文件
        if not record:
            record = FileModel.from_domain(file)
            self.db_session.add(record)
            return

        # 3.文件存在则直接更新文件
        record.update_from_domain(file)

    async def get_by_id(self, file_id: str, scope: Optional[OwnerScope] = None) -> Optional[File]:
        """根据传递的文件id获取文件信息"""
        # 1.根据id查询记录是否存在
        stmt = self._apply_scope(select(FileModel).where(FileModel.id == file_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()

        # 2.判断文件记录是否存在返回不同的值
        return record.to_domain() if record is not None else None

    async def list_by_ids(self, file_ids: List[str], scope: Optional[OwnerScope] = None) -> List[File]:
        """根据传递的文件id列表批量获取文件信息，并按输入顺序返回。"""
        if not file_ids:
            return []
        stmt = self._apply_scope(select(FileModel).where(FileModel.id.in_(file_ids)), scope)
        result = await self.db_session.execute(stmt)
        records = {record.id: record.to_domain() for record in result.scalars().all()}
        return [records[file_id] for file_id in file_ids if file_id in records]
