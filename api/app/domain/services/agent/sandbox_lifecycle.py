#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sandbox readiness and checkpoint coordination for agent runs."""
import logging
from typing import Optional

from app.domain.models.checkpoint import CheckpointAnchorType
from app.domain.models.event import MessageEvent, StepEvent
from app.domain.services.agent.sandbox_provider import SandboxProvider
from app.domain.services.checkpoint_service import CheckpointService

logger = logging.getLogger(__name__)


class SandboxLifecycleCoordinator:
    def __init__(
            self,
            session_id: str,
            sandbox_provider: SandboxProvider,
            checkpoint_service: Optional[CheckpointService] = None,
    ) -> None:
        self._session_id = session_id
        self._sandbox_provider = sandbox_provider
        self._checkpoint_service = checkpoint_service

    async def ensure_ready(self) -> None:
        sandbox = self._sandbox_provider.materialized()
        if sandbox is None:
            return
        await sandbox.ensure_sandbox()

    async def create_user_message_checkpoint(self, event: MessageEvent) -> None:
        if not self._checkpoint_service or event.role != "user" or not event.id:
            return
        sandbox = self._sandbox_provider.materialized()
        try:
            await self._checkpoint_service.create_checkpoint(
                session_id=self._session_id,
                anchor_type=CheckpointAnchorType.USER_MESSAGE,
                anchor_event_id=event.id,
                label=(event.message or "用户消息")[:200],
                sandbox=sandbox,
            )
        except Exception as exc:
            logger.warning("创建用户消息还原点失败: %s", exc)

    async def create_step_checkpoint(self, event: StepEvent) -> None:
        if not self._checkpoint_service or not event.id:
            return
        sandbox = self._sandbox_provider.materialized()
        try:
            step_label = event.step.description if event.step else "执行步骤"
            await self._checkpoint_service.create_checkpoint(
                session_id=self._session_id,
                anchor_type=CheckpointAnchorType.STEP,
                anchor_event_id=event.id,
                label=step_label[:200],
                sandbox=sandbox,
            )
        except Exception as exc:
            logger.warning("创建步骤还原点失败: %s", exc)

    async def create_tool_checkpoint(self, tool_name: str, tool_call_id: str) -> None:
        if not self._checkpoint_service:
            return
        sandbox = self._sandbox_provider.materialized()
        try:
            await self._checkpoint_service.create_checkpoint(
                session_id=self._session_id,
                anchor_type=CheckpointAnchorType.STEP,
                anchor_event_id=tool_call_id,
                label=f"before {tool_name}"[:200],
                sandbox=sandbox,
            )
        except Exception as exc:
            logger.warning("创建工具前还原点失败: %s", exc)
