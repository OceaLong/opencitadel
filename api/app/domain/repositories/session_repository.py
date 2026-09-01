from datetime import datetime
from typing import Protocol

from app.domain.models.file import File
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionStatus


class SessionRepository(Protocol):
    """会话仓库协议定义"""

    async def save(self, session: Session) -> None:
        """存储或更新传递进来的会话"""
        ...

    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        scope: OwnerScope | None = None,
        search: str | None = None,
    ) -> list[Session]:
        """获取所有会话列表信息；``search`` 非空时按标题/最新消息做关键词过滤"""
        ...

    async def count(self) -> int:
        """Count all sessions platform-wide."""
        ...

    async def count_created_between(
        self,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        """Count *Agent-mode* sessions created within the given window
        (governance-window activity check). Ask-mode sessions are excluded --
        they never plan or invoke gated tools, so they cannot be evidence of
        a formal approval being requested or not."""
        ...

    async def get_by_id(self, session_id: str, scope: OwnerScope | None = None) -> Session | None:
        """根据传递的会话id查询会话"""
        ...

    async def exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        ...

    async def get_metadata(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> Session | None:
        """仅加载会话元数据（不含 memories/files）"""
        ...

    async def lock_by_id(
        self,
        session_id: str,
        scope: OwnerScope | None = None,
    ) -> Session | None:
        """Scope-filter and lock the session row for transactional commands."""
        ...

    async def get_files(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> list[File] | None:
        """仅加载会话文件列表；会话不存在时返回 None"""
        ...

    async def delete_by_id(self, session_id: str) -> None:
        """根据传递的会话id物理删除会话（purge 的底层原语）"""
        ...

    async def list_deleted(
        self,
        limit: int = 100,
        offset: int = 0,
        scope: OwnerScope | None = None,
    ) -> list[Session]:
        """回收站：仅返回已软删（``deleted_at`` 非空）的会话，owner 作用域内。"""
        ...

    async def soft_delete(self, session_id: str, scope: OwnerScope | None = None) -> bool:
        """软删除：设置 ``deleted_at``。仅命中未删除的行；返回是否命中。"""
        ...

    async def restore(self, session_id: str, scope: OwnerScope | None = None) -> bool:
        """恢复：清空 ``deleted_at``。仅命中回收站中的行；返回是否命中。"""
        ...

    async def purge(self, session_id: str, scope: OwnerScope | None = None) -> bool:
        """清除：物理删除回收站中的会话（``deleted_at`` 非空）；返回是否命中。"""
        ...

    async def update_title(self, session_id: str, title: str) -> None:
        """根据传递的会话id+标题更新会话信息"""
        ...

    async def update_latest_message(
        self, session_id: str, message: str, timestamp: datetime
    ) -> None:
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

    async def update_session_config(
        self,
        session_id: str,
        model_id: str | None = None,
        skill_id: str | None = None,
        thinking_enabled: bool | None = None,
        operator_domains: list[str] | None = None,
        clear_model: bool = False,
        clear_skill: bool = False,
    ) -> None:
        """更新会话级模型、Skill、思考模式与网络边界配置。"""
        ...

    async def add_file(self, session_id: str, file: File) -> None:
        """往会话中新增文件"""
        ...

    async def remove_file(self, session_id: str, file_id: str) -> None:
        """根据传递的会话id+文件id移除文件"""
        ...

    async def get_file_by_path(self, session_id: str, filepath: str) -> File | None:
        """查询会话中的文件信息"""
        ...
