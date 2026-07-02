#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Protocol, List, Optional, Dict, Any, Tuple

from app.domain.models.event import BaseEvent
from app.domain.models.file import File
from app.domain.models.memory import Memory
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionStatus


class SessionRepository(Protocol):
    """会话仓库协议定义"""

    async def save(self, session: Session) -> None:
        """存储或更新传递进来的会话"""
        ...

    async def get_all(self, limit: int = 100, offset: int = 0, scope: Optional[OwnerScope] = None) -> List[Session]:
        """获取所有会话列表信息"""
        ...

    async def list_recoverable_running(
            self,
            limit: int = 100,
            updated_before: Optional[datetime] = None,
    ) -> List[Session]:
        """List RUNNING sessions with active task ids that may need recovery."""
        ...

    async def get_by_id(self, session_id: str, scope: Optional[OwnerScope] = None) -> Optional[Session]:
        """根据传递的会话id查询会话"""
        ...

    async def exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        ...

    async def get_metadata(self, session_id: str, scope: Optional[OwnerScope] = None) -> Optional[Session]:
        """仅加载会话元数据（不含 memories/files）"""
        ...

    async def get_files(self, session_id: str, scope: Optional[OwnerScope] = None) -> Optional[List[File]]:
        """仅加载会话文件列表；会话不存在时返回 None"""
        ...

    async def delete_by_id(self, session_id: str) -> None:
        """根据传递的会话id删除会话"""
        ...

    async def update_title(self, session_id: str, title: str) -> None:
        """根据传递的会话id+标题更新会话信息"""
        ...

    async def update_latest_message(self, session_id: str, message: str, timestamp: datetime) -> None:
        """根据传递的信息更新最新消息"""
        ...

    async def update_unread_message_count(self, session_id: str, count: int) -> None:
        """根据传递的信息更新未读消息数"""
        ...

    async def increment_unread_message_count(self, session_id: str) -> None:
        """根据传递的会话id新增未读消息数"""
        ...

    async def decrement_unread_message_count(self, session_id: str) -> None:
        """根据传递的会话id减少未读消息数"""
        ...

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """根据传递的会话id更新会话状态"""
        ...

    async def set_pending_phase(self, session_id: str, phase: Optional[str]) -> None:
        """更新会话等待恢复的内部阶段"""
        ...

    async def set_pending_metadata(self, session_id: str, metadata: Optional[Dict[str, Any]]) -> None:
        """更新会话门控状态元数据"""
        ...

    async def get_pending_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读取会话门控状态元数据"""
        ...

    async def update_session_config(
            self,
            session_id: str,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            thinking_enabled: Optional[bool] = None,
            clear_model: bool = False,
            clear_skill: bool = False,
    ) -> None:
        """更新会话级模型、Skill与思考模式配置"""
        ...

    async def add_event(
            self,
            session_id: str,
            event: BaseEvent,
            event_data: Optional[Dict[str, Any]] = None,
            seq: Optional[int] = None,
    ) -> int:
        """往会话中新增事件，返回全局 seq"""
        ...

    async def add_events(self, session_id: str, events: List[BaseEvent]) -> None:
        """批量新增会话事件"""
        ...

    async def add_event_payloads(
            self,
            session_id: str,
            payloads: List[Tuple[BaseEvent, Dict[str, Any]]],
    ) -> None:
        """批量新增已序列化的会话事件"""
        ...

    async def list_events(
            self,
            session_id: str,
            after: Optional[int] = None,
            before: Optional[int] = None,
            limit: int = 100,
            latest: bool = False,
    ) -> List[Tuple[int, BaseEvent]]:
        """按游标分页查询会话事件"""
        ...

    async def has_events_before(self, session_id: str, seq: int) -> bool:
        """是否存在早于指定 seq 的会话事件"""
        ...

    async def add_file(self, session_id: str, file: File) -> None:
        """往会话中新增文件"""
        ...

    async def remove_file(self, session_id: str, file_id: str) -> None:
        """根据传递的会话id+文件id移除文件"""
        ...

    async def get_file_by_path(self, session_id: str, filepath: str) -> Optional[File]:
        """查询会话中的文件信息"""
        ...

    async def save_memory(self, session_id: str, agent_name: str, memory: Memory) -> None:
        """更新or创建会话中指定Agent的记忆"""
        ...

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        """根据传递的会话id+Agent名字获取记忆"""
        ...

    async def get_max_event_seq(self, session_id: str) -> Optional[int]:
        """Get the maximum persisted event seq for a session."""
        ...

    async def get_event_seq_by_stream_id(self, session_id: str, stream_id: str) -> Optional[int]:
        """Resolve a persisted event seq by stream/event id."""
        ...

    async def delete_events_from_seq(
            self,
            session_id: str,
            from_seq: int,
            inclusive: bool = True,
    ) -> int:
        """Delete session events from the given seq onward."""
        ...

    async def restore_session_snapshot(
            self,
            session_id: str,
            memories: Dict[str, Any],
            files: List[Dict[str, Any]],
            status: str,
            pending_phase: Optional[str],
    ) -> None:
        """Restore session memories, files and status from a checkpoint snapshot."""
        ...
