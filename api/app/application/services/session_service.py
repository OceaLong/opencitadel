#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import time
from typing import List, Callable, Type, Optional, Tuple

from app.application.dto.session_io import FileReadResult, ShellReadResult
from app.application.errors.exceptions import NotFoundError, ServerRequestsError
from app.domain.external.sandbox import Sandbox
from app.domain.external.session_list_notifier import NoopSessionListNotifier, SessionListNotifierPort
from app.domain.external.task_state_port import TaskStatePort
from app.domain.models.file import File
from app.domain.models.codebase import SessionMode
from app.domain.models.session import Session
from app.domain.models.event import BaseEvent
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

_DELETE_TASK_DRAIN_TIMEOUT_SECONDS = 30.0
_DELETE_TASK_POLL_INTERVAL_SECONDS = 0.5


class SessionService:
    """会话服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            sandbox_cls: Type[Sandbox],
            session_list_notifier: Optional[SessionListNotifierPort] = None,
            task_state_port: Optional[TaskStatePort] = None,
    ) -> None:
        """构造函数，完成会话服务初始化"""
        self._uow_factory = uow_factory
        self._sandbox_cls = sandbox_cls
        self._session_list_notifier = session_list_notifier or NoopSessionListNotifier()
        self._task_state = task_state_port

    async def _wait_for_task_drain(self, task_id: str) -> None:
        if not self._task_state:
            return
        deadline = time.monotonic() + _DELETE_TASK_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            snapshot = await self._task_state.get_runtime_snapshot(task_id)
            if snapshot.get("is_done"):
                return
            await asyncio.sleep(_DELETE_TASK_POLL_INTERVAL_SECONDS)
        logger.warning("等待任务结束超时 task_id=%s", task_id)

    async def create_session(
            self,
            title: str = "新对话",
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            thinking_enabled: bool = False,
            codebase_id: Optional[str] = None,
            knowledge_base_id: Optional[str] = None,
            mode: Optional[SessionMode] = None,
    ) -> Session:
        """创建一个空白的新任务会话"""
        logger.info(f"创建一个空白新任务会话")
        session = Session(
            title=title,
            model_id=model_id,
            skill_id=skill_id,
            thinking_enabled=thinking_enabled,
            codebase_id=codebase_id,
            knowledge_base_id=knowledge_base_id,
            mode=mode or SessionMode.AGENT,
        )
        async with self._uow_factory() as uow:
            await uow.session.save(session)
        await self._session_list_notifier.notify_sessions_changed()
        logger.info(f"成功创建一个新任务会话: {session.id}")
        return session

    async def update_session_config(
            self,
            session_id: str,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            thinking_enabled: Optional[bool] = None,
    ) -> Session:
        async with self._uow_factory() as uow:
            await uow.session.update_session_config(
                session_id,
                model_id=model_id,
                skill_id=skill_id,
                thinking_enabled=thinking_enabled,
                clear_model=model_id == "",
                clear_skill=skill_id == "",
            )
            return await uow.session.get_by_id(session_id)

    async def get_all_sessions(self, limit: int = 100, offset: int = 0) -> List[Session]:
        """获取项目所有任务会话列表"""
        async with self._uow_factory() as uow:
            return await uow.session.get_all(limit=limit, offset=offset)

    async def clear_unread_message_count(self, session_id: str) -> None:
        """清空指定会话未读消息数"""
        logger.info(f"清除会话[{session_id}]未读消息数")
        async with self._uow_factory() as uow:
            await uow.session.update_unread_message_count(session_id, 0)

    async def delete_session(self, session_id: str) -> None:
        """根据传递的会话id删除任务会话"""
        # 1.先检查会话是否存在
        logger.info(f"正在删除会话, 会话id: {session_id}")
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            logger.error(f"会话[{session_id}]不存在, 删除失败")
            raise NotFoundError(f"会话[{session_id}]不存在, 删除失败")

        if session.task_id and self._task_state:
            await self._task_state.request_cancel(session.task_id)
            await self._wait_for_task_drain(session.task_id)

        # 2.销毁关联 sandbox 后删除会话
        if session.sandbox_id:
            try:
                sandbox = await self._sandbox_cls.get(session.sandbox_id)
                if sandbox:
                    await sandbox.destroy()
            except Exception as e:
                logger.warning("删除会话时销毁 sandbox 失败 session=%s: %s", session_id, e)

        async with self._uow_factory() as uow:
            await uow.session.delete_by_id(session_id)
        await self._session_list_notifier.notify_sessions_changed()
        logger.info(f"删除会话[{session_id}]成功")

    async def get_session(self, session_id: str) -> Session:
        """获取指定会话详情信息"""
        async with self._uow_factory() as uow:
            return await uow.session.get_by_id(session_id)

    async def get_session_events(
            self,
            session_id: str,
            after: Optional[int] = None,
            before: Optional[int] = None,
            limit: int = 100,
            latest: bool = False,
    ) -> List[Tuple[int, BaseEvent]]:
        """分页获取会话事件"""
        async with self._uow_factory() as uow:
            if not await uow.session.exists(session_id):
                raise NotFoundError("该会话不存在，请核实后重试")
            return await uow.session.list_events(
                session_id,
                after=after,
                before=before,
                limit=limit,
                latest=latest,
            )

    async def has_events_before(self, session_id: str, seq: int) -> bool:
        async with self._uow_factory() as uow:
            return await uow.session.has_events_before(session_id, seq)

    async def get_session_files(self, session_id: str) -> List[File]:
        """根据传递的会话id获取指定会话的文件列表信息"""
        logger.info(f"获取指定会话[{session_id}]下的文件列表信息")
        async with self._uow_factory() as uow:
            files = await uow.session.get_files(session_id)
        if files is None:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")
        return files

    async def read_file(self, session_id: str, filepath: str) -> FileReadResult:
        """根据传递的信息查看会话中指定文件的内容"""
        # 1.检查会话是否存在
        logger.info(f"获取会话[{session_id}]中的文件内容, 文件路径: {filepath}")
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_cls.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        # 3.调用沙箱读取文件内容
        result = await sandbox.read_file(filepath)
        if result.success:
            return FileReadResult(**result.data)

        raise ServerRequestsError(result.message)

    async def read_shell_output(self, session_id: str, shell_session_id: str) -> ShellReadResult:
        """根据传递的任务会话id+Shell会话id获取Shell执行结果"""
        # 1.检查会话是否存在
        logger.info(f"获取会话[{session_id}]中的Shell内容输出, Shell标识符: {shell_session_id}")
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_cls.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        # 3.调用沙箱查看shell内容
        result = await sandbox.read_shell_output(session_id=shell_session_id, console=True)
        if result.success:
            return ShellReadResult(**result.data)

        raise ServerRequestsError(result.message)

    async def get_vnc_url(self, session_id: str) -> str:
        """获取指定会话的vnc链接"""
        # 1.检查会话是否存在
        logger.info(f"获取会话[{session_id}]的VNC链接")
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_cls.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        return sandbox.vnc_url
