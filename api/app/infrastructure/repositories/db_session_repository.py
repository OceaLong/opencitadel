#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, delete, update, func, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import TypeAdapter

from app.domain.models.event import BaseEvent, Event
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.file import File
from app.domain.models.memory import Memory
from app.domain.models.session import Session, SessionStatus
from app.domain.repositories.session_repository import SessionRepository
from app.infrastructure.models import SessionEventModel, SessionModel


class DBSessionRepository(SessionRepository):
    """基于Postgres数据库的会话仓库"""

    def __init__(self, db_session: AsyncSession) -> None:
        """构造函数，完成数据仓库的初始化"""
        self.db_session = db_session

    async def save(self, session: Session) -> None:
        """根据传递的领域模型更新或者新增会话"""
        # 1.根据id查询会话是否存在
        stmt = select(SessionModel).where(SessionModel.id == session.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()

        # 2.如果会话不存在则新建会话
        if not record:
            record = SessionModel.from_domain(session)
            self.db_session.add(record)
            return

        # 3.会话存在则更新会话
        record.update_from_domain(session)

    async def get_all(self, limit: int = 100, offset: int = 0) -> List[Session]:
        """获取所有会话列表"""
        # 1.构建sql查询所有记录
        stmt = (
            select(SessionModel)
            .order_by(SessionModel.latest_message_at.desc().nullslast())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()

        # 2.将数据循环遍历成Session
        return [record.to_domain() for record in records]

    async def get_by_id(self, session_id: str) -> Optional[Session]:
        """根据id查询会话"""
        # 1.根据id查询会话是否存在
        stmt = select(SessionModel).where(SessionModel.id == session_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()

        # 2.判断会话记录是否存在并返回
        if record is None:
            return None

        return record.to_domain()

    async def delete_by_id(self, session_id: str) -> None:
        """根据传递的id删除会话"""
        # 1.构建删除语句
        stmt = delete(SessionModel).where(SessionModel.id == session_id)

        # 2.执行sql无需检查是否删除
        await self.db_session.execute(stmt)

    async def update_title(self, session_id: str, title: str) -> None:
        """更新会话标题"""
        # 1.构建更新语句并执行
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(title=title)
        )
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def update_latest_message(self, session_id: str, message: str, timestamp: datetime) -> None:
        """更新会话最新消息"""
        # 1.构建更新语句并执行
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                latest_message=message,
                latest_message_at=timestamp,
            )
        )
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def add_event(
            self,
            session_id: str,
            event: BaseEvent,
            event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """往会话中新增事件"""
        # 1.检查会话存在，保持与旧 JSONB 更新路径一致的错误语义
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists = await self.db_session.scalar(exists_stmt)
        if exists is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        # 2.追加写入独立事件表，避免重写 sessions.events JSONB 数组
        payload = event_data or event.model_dump(mode="json")
        self.db_session.add(
            SessionEventModel(
                session_id=session_id,
                stream_id=payload.get("id"),
                type=payload.get("type", event.type),
                payload=payload,
                created_at=event.created_at,
            )
        )

    async def add_events(self, session_id: str, events: List[BaseEvent]) -> None:
        """批量新增事件"""
        if not events:
            return
        payloads = [(event, event.model_dump(mode="json")) for event in events]
        await self.add_event_payloads(session_id, payloads)

    async def add_event_payloads(
            self,
            session_id: str,
            payloads: List[Tuple[BaseEvent, Dict[str, Any]]],
    ) -> None:
        """批量新增已序列化事件"""
        if not payloads:
            return
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists = await self.db_session.scalar(exists_stmt)
        if exists is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        self.db_session.add_all([
            SessionEventModel(
                session_id=session_id,
                stream_id=event_data.get("id"),
                type=event_data.get("type", event.type),
                payload=event_data,
                created_at=event.created_at,
            )
            for event, event_data in payloads
        ])

    async def list_events(
            self,
            session_id: str,
            after: Optional[int] = None,
            limit: int = 100,
    ) -> List[Tuple[int, BaseEvent]]:
        """按游标分页获取会话事件"""
        stmt = (
            select(SessionEventModel)
            .where(SessionEventModel.session_id == session_id)
            .order_by(SessionEventModel.seq.asc())
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(SessionEventModel.seq > after)
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()
        adapter = TypeAdapter(Event)
        return [
            (record.seq, adapter.validate_python(upgrade_event_payload(record.payload)))
            for record in records
        ]

    async def add_file(self, session_id: str, file: File) -> None:
        """往会话中新增文件"""
        # 1.将file序列化为json
        file_data = file.model_dump(mode="json")

        # 2.构建原子更新语句并执行
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                files=func.coalesce(SessionModel.files, cast([], JSONB)) + cast([file_data], JSONB),
            )
        )
        result = await self.db_session.execute(stmt)

        # 3.检查是否新增成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def remove_file(self, session_id: str, file_id: str) -> None:
        """移除会话中的指定文件"""
        # 1.查询会话记录并加锁
        stmt = select(SessionModel).where(SessionModel.id == session_id).with_for_update()
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()

        # 2.检查会话记录是否存在
        if not record:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        # 3.会话记录存在在，则在内存中过滤files
        if not record.files:
            return
        original_length = len(record.files)
        new_files = [file for file in record.files if file.get("id") != file_id]

        # 4.判断文件长度是否有变化
        if len(new_files) == original_length:
            return

        # 5.更新数据
        record.files = new_files

    async def get_file_by_path(self, session_id: str, filepath: str) -> Optional[File]:
        """根据文件路径获取文件信息"""
        # 1.构建语句查询文件列表
        stmt = select(SessionModel.files).where(SessionModel.id == session_id)
        result = await self.db_session.execute(stmt)
        files = result.scalar_one_or_none()

        # 2.判断是否为空，如果不存在则返回None
        if not files:
            return None

        # 3.遍历查找数据，如果最后没找到则返回空
        for file in files:
            if file.get("filepath", "") == filepath:
                return File(**file)

        return None

    async def update_session_config(
            self,
            session_id: str,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            thinking_enabled: Optional[bool] = None,
            clear_model: bool = False,
            clear_skill: bool = False,
    ) -> None:
        values = {}
        if clear_model:
            values["model_id"] = None
        elif model_id is not None:
            values["model_id"] = model_id
        if clear_skill:
            values["skill_id"] = None
        elif skill_id is not None:
            values["skill_id"] = skill_id
        if thinking_enabled is not None:
            values["thinking_enabled"] = thinking_enabled
        if not values:
            return
        stmt = update(SessionModel).where(SessionModel.id == session_id).values(**values)
        result = await self.db_session.execute(stmt)
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """更新会话状态"""
        # 1.构建更新语句并执行
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(status=status.value)
        )
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def set_pending_phase(self, session_id: str, phase: Optional[str]) -> None:
        """更新会话等待恢复的内部阶段"""
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(pending_phase=phase)
        )
        result = await self.db_session.execute(stmt)
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def update_unread_message_count(self, session_id: str, count: int) -> None:
        """更新会话的未读消息数"""
        # 1.构建更新语句并执行
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(unread_message_count=count)
        )
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def increment_unread_message_count(self, session_id: str) -> None:
        """新增会话的未读消息数"""
        # 1.构建新增未读消息数语句并更新
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                unread_message_count=func.coalesce(SessionModel.unread_message_count, 0) + 1,
            )
        )
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def decrement_unread_message_count(self, session_id: str) -> None:
        """将会话中的未读消息数-1"""
        # 1.构建新增未读消息数语句并更新
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                # 2.核心逻辑：GREATEST((当前值-1), 0)避免出现负数
                unread_message_count=func.greatest(
                    func.coalesce(SessionModel.unread_message_count, 0) - 1,
                    0
                )
            )
        )
        result = await self.db_session.execute(stmt)

        # 3.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def save_memory(self, session_id: str, agent_name: str, memory: Memory) -> None:
        """存储或者更新会话中的记忆(字典直接覆盖)"""
        # 1.将memory转换成为json结构
        memory_data = memory.model_dump(mode="json")

        # 2.构建要打补丁的字典
        patch_data = {agent_name: memory_data}

        # 3.执行合并更新
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                memories=func.coalesce(SessionModel.memories, cast({}, JSONB)) + cast(patch_data, JSONB),
            )
        )
        result = await self.db_session.execute(stmt)

        # 4.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        """获取指定会话的agent记忆信息"""
        # 1.查询会话记忆信息
        stmt = (
            select(SessionModel.memories[agent_name])
            .where(SessionModel.id == session_id)
        )
        result = await self.db_session.execute(stmt)
        memory_data = result.scalar_one_or_none()

        # 2.如果存在记忆则直接返回
        if memory_data:
            return Memory(**memory_data)

        # 3.如果记忆不存在，则构建一个空记忆后返回
        return Memory(messages=[])
