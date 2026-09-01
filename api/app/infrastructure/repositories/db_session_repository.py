from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.file import File
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session, SessionStatus
from app.domain.models.session_mode import SessionMode
from app.domain.repositories.session_repository import SessionRepository
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.session_file_attachment import (
    SessionFileAttachmentModel,
)
from app.infrastructure.models.session_resource_binding import (
    SessionResourceBindingORM,
)


class DBSessionRepository(SessionRepository):
    """基于Postgres数据库的会话仓库"""

    def __init__(self, db_session: AsyncSession) -> None:
        """构造函数，完成数据仓库的初始化"""
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(SessionModel.team_id == scope.team_id)
        return stmt.where(
            SessionModel.owner_user_id == scope.user_id, SessionModel.team_id.is_(None)
        )

    async def count_created_between(
        self,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        """Count *Agent-mode* sessions created within the window. Ask-mode
        (quick Q&A, no planning/tool-gating) sessions are excluded because
        this method backs compliance's ``agent_session_count`` -- it must
        answer "were there Agent sessions whose formal approvals could have
        fired", not "was the product used at all"."""
        stmt = (
            select(func.count())
            .select_from(SessionModel)
            .where(SessionModel.mode == SessionMode.AGENT.value)
        )
        if start_at:
            stmt = stmt.where(SessionModel.created_at >= start_at)
        if end_at:
            stmt = stmt.where(SessionModel.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def _load_files(self, session_id: str) -> list[File]:
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
        session.files = await self._load_files(record.id)
        session.resource_bindings = await self._load_resource_bindings(record.id)
        return session

    async def _load_resource_bindings(
        self,
        session_id: str,
    ):
        return (await self._load_resource_bindings_for_sessions([session_id])).get(session_id, [])

    async def _load_resource_bindings_for_sessions(
        self,
        session_ids: list[str],
    ) -> dict[str, list]:
        bindings_by_session = {session_id: [] for session_id in session_ids}
        if not session_ids:
            return bindings_by_session
        result = await self.db_session.execute(
            select(SessionResourceBindingORM)
            .where(
                SessionResourceBindingORM.session_id.in_(session_ids),
                SessionResourceBindingORM.is_current.is_(True),
            )
            .order_by(
                SessionResourceBindingORM.session_id.asc(),
                SessionResourceBindingORM.resource_kind.asc(),
            )
        )
        for record in result.scalars().all():
            bindings_by_session[record.session_id].append(record.to_domain().to_projection())
        return bindings_by_session

    async def _persist_files(self, session_id: str, files: list[File]) -> None:
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
            await self._persist_files(session.id, session.files)
            await self.db_session.flush()
            return

        # 3.会话存在则仅更新元数据（files 由 add_file 专用路径维护）
        record.update_from_domain(session)

    async def get_all(
        self, limit: int = 100, offset: int = 0, scope: OwnerScope | None = None
    ) -> list[Session]:
        """获取所有会话列表（列表视图不加载 memories/files，避免 N+1）"""
        stmt = self._apply_scope(select(SessionModel), scope)
        stmt = (
            stmt.order_by(SessionModel.latest_message_at.desc().nullslast())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        records = result.scalars().all()
        bindings = await self._load_resource_bindings_for_sessions(
            [record.id for record in records]
        )
        sessions = [record.to_domain() for record in records]
        for session in sessions:
            session.resource_bindings = bindings[session.id]
        return sessions

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(SessionModel))
        return int(result.scalar_one() or 0)

    async def exists(self, session_id: str) -> bool:
        stmt = select(SessionModel.id).where(SessionModel.id == session_id).limit(1)
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_metadata(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> Session | None:
        stmt = self._apply_scope(select(SessionModel).where(SessionModel.id == session_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        session = record.to_domain()
        session.resource_bindings = await self._load_resource_bindings(session.id)
        return session

    async def lock_by_id(
        self,
        session_id: str,
        scope: OwnerScope | None = None,
    ) -> Session | None:
        stmt = self._apply_scope(
            select(SessionModel).where(SessionModel.id == session_id),
            scope,
        ).with_for_update()
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        session = record.to_domain()
        session.resource_bindings = await self._load_resource_bindings(session.id)
        return session

    async def get_files(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> list[File] | None:
        if await self.get_metadata(session_id, scope=scope) is None:
            return None
        return await self._load_files(session_id)

    async def get_by_id(self, session_id: str, scope: OwnerScope | None = None) -> Session | None:
        """根据id查询会话"""
        stmt = self._apply_scope(select(SessionModel).where(SessionModel.id == session_id), scope)
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
        stmt = update(SessionModel).where(SessionModel.id == session_id).values(title=title)
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def update_latest_message(
        self, session_id: str, message: str, timestamp: datetime
    ) -> None:
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

    async def get_file_by_path(self, session_id: str, filepath: str) -> File | None:
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
        model_id: str | None = None,
        skill_id: str | None = None,
        thinking_enabled: bool | None = None,
        operator_domains: list | None = None,
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
        if operator_domains is not None:
            values["operator_domains"] = operator_domains
        if not values:
            return
        stmt = update(SessionModel).where(SessionModel.id == session_id).values(**values)
        result = await self.db_session.execute(stmt)
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """更新会话状态"""
        # 1.构建更新语句并执行
        stmt = update(SessionModel).where(SessionModel.id == session_id).values(status=status.value)
        result = await self.db_session.execute(stmt)

        # 2.检查是否更新成功
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
                    func.coalesce(SessionModel.unread_message_count, 0) - 1, 0
                )
            )
        )
        result = await self.db_session.execute(stmt)

        # 3.检查是否更新成功
        if result.rowcount == 0:
            raise ValueError(f"会话[{session_id}]不存在，请核实后重试")
