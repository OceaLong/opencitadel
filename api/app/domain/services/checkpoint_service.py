#!/usr/bin/env python
# -*- coding: utf-8 -*-
import io
import logging
import uuid
from datetime import datetime
from typing import Callable, List, Optional, Type

from app.domain.external.sandbox import Sandbox
from app.domain.models.checkpoint import (
    Checkpoint,
    CheckpointAnchorType,
    SessionStateSnapshot,
)
from app.domain.models.event import MessageEvent
from app.domain.models.memory import Memory
from app.domain.models.session import Session, SessionStatus
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.task_state_port import TaskStatePort
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class CheckpointService:
    """Create and restore session checkpoints for per-step rollback."""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            object_storage: ObjectStoragePort,
            sandbox_cls: Type[Sandbox],
            task_state_port: TaskStatePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_storage = object_storage
        self._sandbox_cls = sandbox_cls
        self._task_state = task_state_port

    async def create_checkpoint(
            self,
            session_id: str,
            anchor_type: CheckpointAnchorType,
            anchor_event_id: str,
            label: str,
            sandbox: Optional[Sandbox] = None,
    ) -> Checkpoint:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
            if not session:
                raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

            seq_before = await uow.session.get_max_event_seq(session_id)
            checkpoint_id = str(uuid.uuid4())
            snapshot_key: Optional[str] = None
            browser_snapshot_key: Optional[str] = None

            active_sandbox = sandbox
            if active_sandbox is None and session.sandbox_id:
                active_sandbox = await self._sandbox_cls.get(session.sandbox_id)

            if active_sandbox is not None:
                try:
                    await active_sandbox.ensure_sandbox()
                    snapshot_bytes = await active_sandbox.create_workspace_snapshot(checkpoint_id)
                    snapshot_key = f"checkpoints/{session_id}/{checkpoint_id}.tgz"
                    await self._object_storage.put_bytes(snapshot_key, snapshot_bytes)
                except Exception as exc:
                    logger.warning(
                        "会话[%s]创建沙箱快照失败，将仅保存对话状态: %s",
                        session_id,
                        exc,
                    )
                try:
                    if session.operator_scope and active_sandbox is not None:
                        browser_bytes = await active_sandbox.create_browser_profile_snapshot(checkpoint_id)
                        browser_snapshot_key = f"checkpoints/{session_id}/{checkpoint_id}_browser.tgz"
                        await self._object_storage.put_bytes(browser_snapshot_key, browser_bytes)
                except Exception as exc:
                    logger.warning(
                        "会话[%s]创建浏览器快照失败，将仅保存工作区快照: %s",
                        session_id,
                        exc,
                    )
                    browser_snapshot_key = None

            checkpoint = Checkpoint(
                id=checkpoint_id,
                session_id=session_id,
                anchor_type=anchor_type,
                anchor_event_id=anchor_event_id,
                label=label[:500],
                seq_before=seq_before,
                memories_snapshot={
                    agent: (
                        memory.model_dump(mode="json")
                        if isinstance(memory, Memory)
                        else memory
                    )
                    for agent, memory in (session.memories or {}).items()
                },
                files_snapshot=[file.model_dump(mode="json") for file in session.files],
                session_state=SessionStateSnapshot(
                    status=session.status.value,
                    pending_phase=session.pending_phase,
                ),
                sandbox_snapshot_key=snapshot_key,
                browser_snapshot_key=browser_snapshot_key,
            )
            await uow.checkpoint.save(checkpoint)
            return checkpoint

    async def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        async with self._uow_factory() as uow:
            return await uow.checkpoint.list_by_session(session_id)

    async def restore(self, session_id: str, checkpoint_id: str) -> None:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
            if not session:
                raise ValueError(f"会话[{session_id}]不存在，请核实后重试")

            checkpoint = await uow.checkpoint.get_by_id(checkpoint_id)
            if not checkpoint or checkpoint.session_id != session_id:
                raise ValueError(f"还原点[{checkpoint_id}]不存在，请核实后重试")

            if session.status == SessionStatus.RUNNING and session.task_id:
                await self._task_state.request_cancel(session.task_id)

            anchor_seq = await uow.session.get_event_seq_by_stream_id(
                session_id,
                checkpoint.anchor_event_id,
            )
            if anchor_seq is None:
                raise ValueError("无法定位还原点对应的事件，请刷新后重试")

            await uow.session.delete_events_from_seq(session_id, anchor_seq, inclusive=True)
            await uow.session.restore_session_snapshot(
                session_id=session_id,
                memories=checkpoint.memories_snapshot,
                files=checkpoint.files_snapshot,
                status=checkpoint.session_state.status,
                pending_phase=checkpoint.session_state.pending_phase,
            )
            await uow.checkpoint.delete_from(session_id, checkpoint.created_at, inclusive=True)

            await self._restore_latest_message(uow, session_id)
            restored_session = await uow.session.get_by_id(session_id)
            sandbox_id = restored_session.sandbox_id if restored_session else session.sandbox_id

        if checkpoint.sandbox_snapshot_key and sandbox_id:
            sandbox = await self._sandbox_cls.get(sandbox_id)
            if sandbox is None:
                sandbox = await self._sandbox_cls.create()
                async with self._uow_factory() as uow:
                    restored = await uow.session.get_by_id(session_id)
                    if restored:
                        restored.sandbox_id = sandbox.id
                        await uow.session.save(restored)

            if sandbox is not None:
                await sandbox.ensure_sandbox()
                snapshot_bytes = await self._object_storage.get_bytes(checkpoint.sandbox_snapshot_key)
                await sandbox.restore_workspace_snapshot(
                    checkpoint_id,
                    io.BytesIO(snapshot_bytes),
                )

        if checkpoint.browser_snapshot_key and sandbox_id:
            sandbox = await self._sandbox_cls.get(sandbox_id)
            if sandbox is not None:
                await sandbox.ensure_sandbox()
                browser_bytes = await self._object_storage.get_bytes(checkpoint.browser_snapshot_key)
                await sandbox.restore_browser_profile_snapshot(
                    checkpoint_id,
                    io.BytesIO(browser_bytes),
                )

        if session.status == SessionStatus.RUNNING:
            async with self._uow_factory() as uow:
                await uow.session.update_status(session_id, SessionStatus.CANCELLED)

    async def _restore_latest_message(self, uow: IUnitOfWork, session_id: str) -> None:
        records = await uow.session.list_events(session_id, limit=500)
        latest_message = ""
        latest_timestamp = None
        for _, event in reversed(records):
            if isinstance(event, MessageEvent) and event.message:
                latest_message = event.message
                latest_timestamp = event.created_at
                break

        if latest_timestamp is not None:
            await uow.session.update_latest_message(session_id, latest_message, latest_timestamp)
        else:
            await uow.session.update_latest_message(session_id, "", datetime.now())

    async def resume_latest_checkpoint(self, session_id: str) -> Optional[Checkpoint]:
        """Restore the latest checkpoint boundary without deleting events.

        Automated crash recovery uses this as an at-least-once replay boundary:
        durable UI history remains intact, while agent memory/files/sandbox are
        moved back to the latest safe execution point before re-dispatch.
        """
        logger.info("开始恢复最新 checkpoint: session_id=%s", session_id)
        try:
            async with self._uow_factory() as uow:
                session = await uow.session.get_by_id(session_id)
                if not session:
                    logger.warning("恢复 checkpoint 失败，会话不存在: session_id=%s", session_id)
                    return None

                checkpoints = await uow.checkpoint.list_by_session(session_id)
                if not checkpoints:
                    logger.warning("恢复 checkpoint 失败，无可用 checkpoint: session_id=%s", session_id)
                    return None
                checkpoint = await uow.checkpoint.get_by_id(checkpoints[-1].id)
                if not checkpoint:
                    logger.warning(
                        "恢复 checkpoint 失败，checkpoint 记录缺失: session_id=%s",
                        session_id,
                    )
                    return None

                await uow.session.restore_session_snapshot(
                    session_id=session_id,
                    memories=checkpoint.memories_snapshot,
                    files=checkpoint.files_snapshot,
                    status=SessionStatus.RUNNING.value,
                    pending_phase=checkpoint.session_state.pending_phase,
                )
                restored_session = await uow.session.get_by_id(session_id)
                sandbox_id = restored_session.sandbox_id if restored_session else session.sandbox_id

            if checkpoint.sandbox_snapshot_key and sandbox_id:
                sandbox = await self._sandbox_cls.get(sandbox_id)
                if sandbox is None:
                    sandbox = await self._sandbox_cls.create()
                    async with self._uow_factory() as uow:
                        restored = await uow.session.get_by_id(session_id)
                        if restored:
                            restored.sandbox_id = sandbox.id
                            await uow.session.save(restored)

                if sandbox is not None:
                    await sandbox.ensure_sandbox()
                    snapshot_bytes = await self._object_storage.get_bytes(checkpoint.sandbox_snapshot_key)
                    await sandbox.restore_workspace_snapshot(
                        checkpoint.id,
                        io.BytesIO(snapshot_bytes),
                    )

            if checkpoint.browser_snapshot_key and sandbox_id:
                sandbox = await self._sandbox_cls.get(sandbox_id)
                if sandbox is not None:
                    await sandbox.ensure_sandbox()
                    browser_bytes = await self._object_storage.get_bytes(checkpoint.browser_snapshot_key)
                    await sandbox.restore_browser_profile_snapshot(
                        checkpoint.id,
                        io.BytesIO(browser_bytes),
                    )

            logger.info(
                "checkpoint 恢复成功: session_id=%s checkpoint_id=%s",
                session_id,
                checkpoint.id,
            )
            return checkpoint
        except Exception as exc:
            logger.exception(
                "checkpoint 恢复失败: session_id=%s error=%s",
                session_id,
                exc,
            )
            raise
