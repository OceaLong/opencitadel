#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, delete, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import TypeAdapter

from app.domain.models.event import BaseEvent, Event
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.file import File
from app.domain.models.memory import Memory
from app.domain.models.session import Session, SessionStatus
from app.domain.external.session_list_notifier import NoopSessionListNotifier, SessionListNotifierPort
from app.domain.repositories.session_repository import SessionRepository
from app.infrastructure.models import (
    SessionAgentMemoryModel,
    SessionEventModel,
    SessionFileAttachmentModel,
    SessionModel,
)


class DBSessionRepository(SessionRepository):
    """基于Postgres数据库的会话仓库"""

    def __init__(
            self,
            db_session: AsyncSession,
            session_list_notifier: Optional[SessionListNotifierPort] = None,
    ) -> None:
        """构造函数，完成数据仓库的初始化"""
        self.db_session = db_session
        self._session_list_notifier = session_list_notifier or NoopSessionListNotifier()

    async def _load_memories(self, session_id: str) -> Dict[str, Memory]:
        stmt = select(SessionAgentMemoryModel).where(
            SessionAgentMemoryModel.session_id == session_id,
        )
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()
        return {
            record.agent_name: Memory(**(record.memory_data or {"messages": []}))
            for record in records
        }

    async def _load_files(self, session_id: str) -> List[File]:
        stmt = (
            select(SessionFileAttachmentModel)
            .where(SessionFileAttachmentModel.session_id == session_id)
            .order_by(SessionFileAttachmentModel.created_at.asc())
        )
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()
        return [
            File(
                id=record.file_id,
                filename=record.filename,
                filepath=record.filepath,
                key=record.key,
                extension=record.extension,
                mime_type=record.mime_type,
                size=record.size,
            )
            for record in records
        ]

    async def _session_from_record(self, record: SessionModel) -> Session:
        session = record.to_domain()
        session.memories = await self._load_memories(record.id)
        session.files = await self._load_files(record.id)
        return session

    async def _persist_memories(self, session_id: str, memories: Dict[str, Memory]) -> None:
        if not memories:
            return
        for agent_name, memory in memories.items():
            memory_data = memory.model_dump(mode="json")
            stmt = (
                pg_insert(SessionAgentMemoryModel)
                .values(
                    session_id=session_id,
                    agent_name=agent_name,
                    memory_data=memory_data,
                )
                .on_conflict_do_update(
                    index_elements=["session_id", "agent_name"],
                    set_={"memory_data": memory_data},
                )
            )
            await self.db_session.execute(stmt)

    async def _persist_files(self, session_id: str, files: List[File]) -> None:
        if not files:
            return
        for file in files:
            stmt = (
                pg_insert(SessionFileAttachmentModel)
                .values(
                    session_id=session_id,
                    file_id=file.id,
                    filename=file.filename,
                    filepath=file.filepath,
                    key=file.key,
                    extension=file.extension,
                    mime_type=file.mime_type,
                    size=file.size,
                )
                .on_conflict_do_update(
                    index_elements=["session_id", "file_id"],
                    set_={
                        "filename": file.filename,
                        "filepath": file.filepath,
                        "key": file.key,
                        "extension": file.extension,
                        "mime_type": file.mime_type,
                        "size": file.size,
                    },
                )
            )
            await self.db_session.execute(stmt)

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
            await self._persist_memories(session.id, session.memories)
            await self._persist_files(session.id, session.files)
            return

        # 3.会话存在则仅更新元数据（memories/files 由 save_memory/add_file 等专用路径维护）
        record.update_from_domain(session)

    async def get_all(self, limit: int = 100, offset: int = 0) -> List[Session]:
        """获取所有会话列表（列表视图不加载 memories/files，避免 N+1）"""
        stmt = (
            select(SessionModel)
            .order_by(SessionModel.latest_message_at.desc().nullslast())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()
        return [record.to_domain() for record in records]

    async def list_recoverable_running(
            self,
            limit: int = 100,
            updated_before: Optional[datetime] = None,
    ) -> List[Session]:
        stmt = (
            select(SessionModel)
            .where(
                SessionModel.status == SessionStatus.RUNNING.value,
                SessionModel.task_id.is_not(None),
                SessionModel.pending_phase.is_(None),
            )
            .order_by(SessionModel.updated_at.asc())
            .limit(max(1, min(limit, 500)))
        )
        if updated_before is not None:
            stmt = stmt.where(SessionModel.updated_at < updated_before)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def exists(self, session_id: str) -> bool:
        stmt = select(SessionModel.id).where(SessionModel.id == session_id).limit(1)
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_metadata(self, session_id: str) -> Optional[Session]:
        stmt = select(SessionModel).where(SessionModel.id == session_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return record.to_domain()

    async def get_files(self, session_id: str) -> Optional[List[File]]:
        if not await self.exists(session_id):
            return None
        return await self._load_files(session_id)

    async def get_by_id(self, session_id: str) -> Optional[Session]:
        """根据id查询会话"""
        stmt = select(SessionModel).where(SessionModel.id == session_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return await self._session_from_record(record)

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

        await self._session_list_notifier.notify_sessions_changed()

    async def add_event(
            self,
            session_id: str,
            event: BaseEvent,
            event_data: Optional[Dict[str, Any]] = None,
            seq: Optional[int] = None,
    ) -> int:
        """往会话中新增事件，返回全局 seq（与 SSE/分页游标一致）"""
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists = await self.db_session.scalar(exists_stmt)
        if exists is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        payload = dict(event_data or event.model_dump(mode="json"))
        if seq is None:
            from app.infrastructure.external.event_seq_allocator import allocate_event_seq
            seq = await allocate_event_seq()
        payload["id"] = str(seq)
        event.id = str(seq)
        record = SessionEventModel(
            seq=seq,
            session_id=session_id,
            stream_id=payload.get("id"),
            type=payload.get("type", event.type),
            payload=payload,
            created_at=event.created_at,
        )
        self.db_session.add(record)
        await self.db_session.flush()
        assigned_seq = int(record.seq)
        event.id = str(assigned_seq)
        return assigned_seq

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

        records = []
        for event, event_data in payloads:
            seq_value: Optional[int] = None
            event_id = event_data.get("id")
            if event_id is not None:
                try:
                    seq_value = int(str(event_id))
                except (TypeError, ValueError):
                    seq_value = None
            if seq_value is None:
                from app.infrastructure.external.event_seq_allocator import allocate_event_seq
                seq_value = await allocate_event_seq()
            event_data["id"] = str(seq_value)
            event.id = str(seq_value)
            records.append(
                SessionEventModel(
                    seq=seq_value,
                    session_id=session_id,
                    stream_id=str(seq_value),
                    type=event_data.get("type", event.type),
                    payload=event_data,
                    created_at=event.created_at,
                ),
            )
        self.db_session.add_all(records)
        await self.db_session.flush()

    async def list_events(
            self,
            session_id: str,
            after: Optional[int] = None,
            before: Optional[int] = None,
            limit: int = 100,
            latest: bool = False,
    ) -> List[Tuple[int, BaseEvent]]:
        """按游标分页获取会话事件（支持正向 after、反向 before、或最近 latest）"""
        adapter = TypeAdapter(Event)

        if latest:
            stmt = (
                select(SessionEventModel)
                .where(SessionEventModel.session_id == session_id)
                .order_by(SessionEventModel.seq.desc())
                .limit(limit)
            )
            result = await self.db_session.execute(stmt)
            records = list(reversed(result.scalars().all()))
        elif before is not None:
            stmt = (
                select(SessionEventModel)
                .where(
                    SessionEventModel.session_id == session_id,
                    SessionEventModel.seq < before,
                )
                .order_by(SessionEventModel.seq.desc())
                .limit(limit)
            )
            result = await self.db_session.execute(stmt)
            records = list(reversed(result.scalars().all()))
        else:
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

        events: List[Tuple[int, BaseEvent]] = []
        for record in records:
            event = adapter.validate_python(upgrade_event_payload(record.payload))
            event.id = str(record.seq)
            events.append((record.seq, event))
        return events

    async def has_events_before(self, session_id: str, seq: int) -> bool:
        stmt = (
            select(SessionEventModel.seq)
            .where(
                SessionEventModel.session_id == session_id,
                SessionEventModel.seq < seq,
            )
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def add_file(self, session_id: str, file: File) -> None:
        """往会话中新增文件"""
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists_result = await self.db_session.execute(exists_stmt)
        if exists_result.scalar_one_or_none() is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        stmt = (
            pg_insert(SessionFileAttachmentModel)
            .values(
                session_id=session_id,
                file_id=file.id,
                filename=file.filename,
                filepath=file.filepath,
                key=file.key,
                extension=file.extension,
                mime_type=file.mime_type,
                size=file.size,
            )
            .on_conflict_do_update(
                index_elements=["session_id", "file_id"],
                set_={
                    "filename": file.filename,
                    "filepath": file.filepath,
                    "key": file.key,
                    "extension": file.extension,
                    "mime_type": file.mime_type,
                    "size": file.size,
                },
            )
        )
        await self.db_session.execute(stmt)

    async def remove_file(self, session_id: str, file_id: str) -> None:
        """移除会话中的指定文件"""
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists_result = await self.db_session.execute(exists_stmt)
        if exists_result.scalar_one_or_none() is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        stmt = (
            delete(SessionFileAttachmentModel)
            .where(SessionFileAttachmentModel.session_id == session_id)
            .where(SessionFileAttachmentModel.file_id == file_id)
        )
        await self.db_session.execute(stmt)

    async def get_file_by_path(self, session_id: str, filepath: str) -> Optional[File]:
        """根据文件路径获取文件信息"""
        stmt = (
            select(SessionFileAttachmentModel)
            .where(SessionFileAttachmentModel.session_id == session_id)
            .where(SessionFileAttachmentModel.filepath == filepath)
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return File(
            id=record.file_id,
            filename=record.filename,
            filepath=record.filepath,
            key=record.key,
            extension=record.extension,
            mime_type=record.mime_type,
            size=record.size,
        )

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
        """存储或者更新会话中的记忆(按 agent_name 单行 upsert)"""
        exists_stmt = select(SessionModel.id).where(SessionModel.id == session_id)
        exists_result = await self.db_session.execute(exists_stmt)
        if exists_result.scalar_one_or_none() is None:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        memory_data = memory.model_dump(mode="json")
        stmt = (
            pg_insert(SessionAgentMemoryModel)
            .values(
                session_id=session_id,
                agent_name=agent_name,
                memory_data=memory_data,
            )
            .on_conflict_do_update(
                index_elements=["session_id", "agent_name"],
                set_={"memory_data": memory_data},
            )
        )
        await self.db_session.execute(stmt)

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        """获取指定会话的agent记忆信息"""
        stmt = (
            select(SessionAgentMemoryModel.memory_data)
            .where(SessionAgentMemoryModel.session_id == session_id)
            .where(SessionAgentMemoryModel.agent_name == agent_name)
        )
        result = await self.db_session.execute(stmt)
        memory_data = result.scalar_one_or_none()
        if memory_data:
            return Memory(**memory_data)
        return Memory(messages=[])

    async def get_max_event_seq(self, session_id: str) -> Optional[int]:
        stmt = (
            select(func.max(SessionEventModel.seq))
            .where(SessionEventModel.session_id == session_id)
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_event_seq_by_stream_id(self, session_id: str, stream_id: str) -> Optional[int]:
        stmt = (
            select(SessionEventModel.seq)
            .where(SessionEventModel.session_id == session_id)
            .where(SessionEventModel.stream_id == stream_id)
            .order_by(SessionEventModel.seq.asc())
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_events_from_seq(
            self,
            session_id: str,
            from_seq: int,
            inclusive: bool = True,
    ) -> int:
        condition = (
            SessionEventModel.seq >= from_seq
            if inclusive
            else SessionEventModel.seq > from_seq
        )
        stmt = (
            delete(SessionEventModel)
            .where(SessionEventModel.session_id == session_id)
            .where(condition)
        )
        result = await self.db_session.execute(stmt)
        return result.rowcount or 0

    async def restore_session_snapshot(
            self,
            session_id: str,
            memories: Dict[str, Any],
            files: List[Dict[str, Any]],
            status: str,
            pending_phase: Optional[str],
    ) -> None:
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(
                status=status,
                pending_phase=pending_phase,
            )
        )
        result = await self.db_session.execute(stmt)
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

        await self.db_session.execute(
            delete(SessionAgentMemoryModel).where(
                SessionAgentMemoryModel.session_id == session_id,
            ),
        )
        await self.db_session.execute(
            delete(SessionFileAttachmentModel).where(
                SessionFileAttachmentModel.session_id == session_id,
            ),
        )

        for agent_name, memory_data in (memories or {}).items():
            await self.db_session.execute(
                pg_insert(SessionAgentMemoryModel).values(
                    session_id=session_id,
                    agent_name=agent_name,
                    memory_data=memory_data,
                ),
            )

        for file_data in files or []:
            await self.db_session.execute(
                pg_insert(SessionFileAttachmentModel).values(
                    session_id=session_id,
                    file_id=file_data.get("id") or file_data.get("file_id"),
                    filename=file_data.get("filename", ""),
                    filepath=file_data.get("filepath", ""),
                    key=file_data.get("key", ""),
                    extension=file_data.get("extension", ""),
                    mime_type=file_data.get("mime_type", ""),
                    size=int(file_data.get("size") or 0),
                ),
            )
