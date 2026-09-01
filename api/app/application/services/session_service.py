import logging
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.application.dto.session_io import FileReadResult, ShellReadResult
from app.application.ports.streams import SessionListPublisher
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.domain.errors import BadRequestError, NotFoundError, ServerRequestsError
from app.domain.external.sandbox import SandboxFactoryPort
from app.domain.models.file import File
from app.domain.models.operator import normalize_operator_domains
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.session import Session
from app.domain.models.session_mode import SessionMode
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class ActiveRunProjection(Protocol):
    async def latest_active_run_id(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
    ) -> UUID | None: ...


class SessionService:
    """会话服务"""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        sandbox_factory: SandboxFactoryPort,
        run_projection: ActiveRunProjection,
        session_list_publisher: SessionListPublisher,
        resource_guard: ResourceGuardService | None = None,
        resource_binding_service: ResourceBindingService | None = None,
    ) -> None:
        """构造函数，完成会话服务初始化"""
        self._uow_factory = uow_factory
        self._sandbox_factory = sandbox_factory
        self._run_projection = run_projection
        self._session_list_publisher = session_list_publisher
        self._resource_guard = resource_guard
        self._resource_binding_service = resource_binding_service

    async def create_session(
        self,
        title: str = "新对话",
        model_id: str | None = None,
        skill_id: str | None = None,
        thinking_enabled: bool = False,
        knowledge_base_id: str | None = None,
        knowledge_base_version_id: str | None = None,
        mode: SessionMode | None = None,
        operator_scope: str | None = None,
        operator_domains: list[str] | None = None,
        scope: OwnerScope | None = None,
    ) -> Session:
        """创建一个空白的新任务会话"""
        logger.info("创建一个空白新任务会话")
        default_mode = SessionMode.ASK if knowledge_base_id else SessionMode.AGENT
        resolved_mode = mode or default_mode
        validated_resources = None
        if self._resource_guard and scope and knowledge_base_id:
            validated_resources = await self._resource_guard.validate_session_request(
                mode=resolved_mode,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_version_id=knowledge_base_version_id,
                scope=scope,
            )
        session = Session(
            title=title,
            model_id=model_id,
            skill_id=skill_id,
            thinking_enabled=thinking_enabled,
            operator_scope=operator_scope,
            operator_domains=list(operator_domains or []),
            owner_user_id=scope.user_id if scope else None,
            team_id=scope.team_id if scope and scope.type == OwnerScopeType.TEAM else None,
            mode=resolved_mode,
        )
        async with self._uow_factory() as uow:
            if model_id and await uow.inference_model.get_by_id(model_id, scope=scope) is None:
                raise NotFoundError("指定模型不存在或无权访问", error_key="errors.modelNotFound")
            if skill_id and await uow.skill.get_by_id(skill_id, scope=scope) is None:
                raise NotFoundError("指定 Skill 不存在或无权访问")
            if (
                knowledge_base_id
                and await uow.knowledge_base.get_kb(knowledge_base_id, scope=scope) is None
            ):
                raise NotFoundError("指定知识库不存在或无权访问")
            await uow.session.save(session)
            if validated_resources and self._resource_binding_service and scope:
                for version in validated_resources.versions:
                    binding = await self._resource_binding_service.bind_initial_resolved(
                        uow,
                        session_id=session.id,
                        resolved=version,
                        scope=scope,
                        actor_id=scope.user_id,
                    )
                    session.resource_bindings.append(binding.to_projection())
            await uow.commit()
        await self._publish_session_list_hint()
        logger.info("成功创建一个新任务会话: %s", session.id)
        return session

    async def update_session_config(
        self,
        session_id: str,
        model_id: str | None = None,
        skill_id: str | None = None,
        thinking_enabled: bool | None = None,
        operator_domains: list | None = None,
        scope: OwnerScope | None = None,
    ) -> Session:
        async with self._uow_factory() as uow:
            current = await uow.session.get_by_id(session_id, scope=scope)
            if current is None:
                raise NotFoundError(
                    "该会话不存在，请核实后重试", error_key="errors.sessionNotFound"
                )
            if (
                model_id
                and model_id != ""
                and await uow.inference_model.get_by_id(model_id, scope=scope) is None
            ):
                raise NotFoundError("指定模型不存在或无权访问", error_key="errors.modelNotFound")
            if (
                skill_id
                and skill_id != ""
                and await uow.skill.get_by_id(skill_id, scope=scope) is None
            ):
                raise NotFoundError("指定 Skill 不存在或无权访问")
            normalized_domains = (
                normalize_operator_domains(operator_domains)
                if operator_domains is not None
                else None
            )
            if current.operator_scope is not None and normalized_domains == []:
                raise ValueError("operator sessions require at least one allowed domain")
            await uow.session.update_session_config(
                session_id,
                model_id=model_id,
                skill_id=skill_id,
                thinking_enabled=thinking_enabled,
                operator_domains=normalized_domains,
                clear_model=model_id == "",
                clear_skill=skill_id == "",
            )
            updated = await uow.session.get_by_id(session_id, scope=scope)
            await uow.commit()
            return updated

    async def get_all_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        scope: OwnerScope | None = None,
        search: str | None = None,
    ) -> list[Session]:
        """获取项目所有任务会话列表；``search`` 非空时按关键词过滤"""
        async with self._uow_factory() as uow:
            return await uow.session.get_all(limit=limit, offset=offset, scope=scope, search=search)

    async def clear_unread_message_count(self, session_id: str) -> None:
        """清空指定会话未读消息数"""
        logger.info("清除会话[%s]未读消息数", session_id)
        async with self._uow_factory() as uow:
            await uow.session.update_unread_message_count(session_id, 0)
            await uow.commit()

    async def delete_session(self, session_id: str, scope: OwnerScope | None = None) -> None:
        """软删除任务会话：置 ``deleted_at``，进入回收站，可恢复。

        证据链完整性要求删除可回溯，因此不再物理删除；物理删除只发生在
        ``purge_session``（回收站手动清除或保留期到期后）。关联 sandbox 是可
        重建的运行时资源，删除时销毁以立即释放；恢复后按需重新拉起。
        保留期清理（软删 30 天后自动 purge）留待调度器挂载。TODO(recycle-bin):
        在 scheduled_job 服务里挂一个保留期清理 tick（scheduler 文件超出本次范围）。
        """
        if scope is None:
            raise ValueError("session deletion requires an owner scope")
        # 1.先检查会话是否存在（普通读路径已过滤软删行，重复删除将报不存在）
        logger.info("正在软删除会话, 会话id: %s", session_id)
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=scope)
        if not session:
            logger.error("会话[%s]不存在, 删除失败", session_id)
            raise NotFoundError(f"会话[{session_id}]不存在, 删除失败")

        active_run_id = await self._run_projection.latest_active_run_id(
            source_entity_type="session",
            source_entity_id=session_id,
            owner_scope=scope,
        )
        if active_run_id is not None:
            raise BadRequestError("会话仍有活动 Run，请先停止并等待进入终态")

        # 2.销毁关联 sandbox 后软删除会话
        if session.sandbox_id:
            try:
                sandbox = await self._sandbox_factory.get(session.sandbox_id)
                if sandbox:
                    await sandbox.destroy()
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("删除会话时销毁 sandbox 失败 session=%s: %s", session_id, e)

        async with self._uow_factory() as uow:
            await uow.session.soft_delete(session_id, scope=scope)
            await uow.commit()
        await self._publish_session_list_hint()
        logger.info("软删除会话[%s]成功", session_id)

    async def list_deleted_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        scope: OwnerScope | None = None,
    ) -> list[Session]:
        """回收站：列出当前 owner 作用域内已软删的会话。"""
        async with self._uow_factory() as uow:
            return await uow.session.list_deleted(limit=limit, offset=offset, scope=scope)

    async def restore_session(self, session_id: str, scope: OwnerScope | None = None) -> None:
        """从回收站恢复会话：清空 ``deleted_at``。"""
        if scope is None:
            raise ValueError("session restore requires an owner scope")
        logger.info("正在恢复会话, 会话id: %s", session_id)
        async with self._uow_factory() as uow:
            restored = await uow.session.restore(session_id, scope=scope)
            if not restored:
                raise NotFoundError(f"回收站中不存在会话[{session_id}]")
            await uow.commit()
        await self._publish_session_list_hint()
        logger.info("恢复会话[%s]成功", session_id)

    async def purge_session(self, session_id: str, scope: OwnerScope | None = None) -> None:
        """物理清除回收站中的会话（不可恢复）。"""
        if scope is None:
            raise ValueError("session purge requires an owner scope")
        logger.info("正在清除会话, 会话id: %s", session_id)
        async with self._uow_factory() as uow:
            purged = await uow.session.purge(session_id, scope=scope)
            if not purged:
                raise NotFoundError(f"回收站中不存在会话[{session_id}]")
            await uow.commit()
        await self._publish_session_list_hint()
        logger.info("清除会话[%s]成功", session_id)

    async def _publish_session_list_hint(self) -> None:
        try:
            await self._session_list_publisher.publish_changed()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Session list hint publication failed after commit: %s", exc)

    async def get_session(self, session_id: str, scope: OwnerScope | None = None) -> Session:
        """获取指定会话详情信息"""
        async with self._uow_factory() as uow:
            return await uow.session.get_by_id(session_id, scope=scope)

    async def get_session_files(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> list[File]:
        """根据传递的会话id获取指定会话的文件列表信息"""
        logger.info("获取指定会话[%s]下的文件列表信息", session_id)
        async with self._uow_factory() as uow:
            files = await uow.session.get_files(session_id, scope=scope)
        if files is None:
            raise NotFoundError(f"当前会话不存在[{session_id}]")
        return files

    async def read_file(
        self, session_id: str, filepath: str, scope: OwnerScope | None = None
    ) -> FileReadResult:
        """根据传递的信息查看会话中指定文件的内容"""
        # 1.检查会话是否存在
        logger.info("获取会话[%s]中的文件内容, 文件路径: %s", session_id, filepath)
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id, scope=scope)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_factory.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        # 3.调用沙箱读取文件内容
        result = await sandbox.read_file(filepath)
        if result.success:
            return FileReadResult(**result.data)

        raise ServerRequestsError(result.message)

    async def read_shell_output(
        self, session_id: str, shell_session_id: str, scope: OwnerScope | None = None
    ) -> ShellReadResult:
        """根据传递的任务会话id+Shell会话id获取Shell执行结果"""
        # 1.检查会话是否存在
        logger.info("获取会话[%s]中的Shell内容输出, Shell标识符: %s", session_id, shell_session_id)
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id, scope=scope)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_factory.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        # 3.调用沙箱查看shell内容
        result = await sandbox.read_shell_output(session_id=shell_session_id, console=True)
        if result.success:
            return ShellReadResult(**result.data)

        raise ServerRequestsError(result.message)

    async def get_vnc_connection(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> tuple[str, dict[str, str]]:
        """获取指定会话的 VNC 链接及连接沙箱数据面所需的鉴权头。

        VNC 现在经沙箱 :8080 上受 token 保护的反向代理转发（不再暴露无认证的
        :5901），因此返回的 headers 必须在 api 侧发起 WebSocket 连接时携带。
        """
        # 1.检查会话是否存在
        logger.info("获取会话[%s]的VNC链接", session_id)
        async with self._uow_factory() as uow:
            session = await uow.session.get_metadata(session_id, scope=scope)
        if not session:
            raise RuntimeError(f"当前会话不存在[{session_id}], 请核实后重试")

        if not session.sandbox_id:
            raise NotFoundError("当前会话无沙箱环境")
        sandbox = await self._sandbox_factory.get(session.sandbox_id)
        if not sandbox:
            raise NotFoundError("当前会话沙箱不存在或已销毁")

        return sandbox.vnc_url, sandbox.vnc_headers
