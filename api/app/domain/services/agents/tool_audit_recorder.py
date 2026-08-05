#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工具调用审计 / 已访问域名的写库逻辑。

从 ``BaseAgent`` 中抽出：把 gate 判定结果落到审计日志与 session
pending_metadata。判定本身委托给 ``tool_gate_policy.ToolGatePolicy``，本类只
负责组装数据并写库。行为与原 ``BaseAgent`` 私有方法完全一致，仅搬迁位置。
"""
import logging
import time
from typing import Any, Callable, Dict

from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.audit_log import AuditLog
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.tool_gate_policy import ToolGatePolicy
from app.domain.utils.audit_redaction import redact_tool_args, summarize_tool_result
from app.domain.utils.hitl import merge_pending_metadata

logger = logging.getLogger(__name__)


class ToolAuditRecorder:
    """封装工具调用审计日志与已访问域名的写库逻辑，行为与原 BaseAgent 私有方法一致。"""

    def __init__(
            self,
            *,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,
            runtime_settings: AgentRuntimeSettings,
            gate_policy: ToolGatePolicy,
    ) -> None:
        # 不变量：本类持有构造时值快照；BaseAgent 对应字段构造后不得重绑定，
        # 否则审计/gate 将静默使用旧值。
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._runtime_settings = runtime_settings
        self._gate_policy = gate_policy

    async def maybe_record_tool_audit(
            self,
            *,
            tool_name: str,
            arguments: Dict[str, Any],
            result: ToolResult,
            started: float,
    ) -> None:
        if not self._gate_policy.should_audit(tool_name):
            return
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            await self.record_tool_audit(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                duration_ms=duration_ms,
                gated=self._gate_policy.compute_gated_flag(tool_name, arguments),
            )
        except Exception:
            logger.exception("写入工具审计失败 session=%s tool=%s", self._session_id, tool_name)

    async def record_tool_audit(
            self,
            *,
            tool_name: str,
            arguments: Dict[str, Any],
            result: ToolResult,
            duration_ms: int,
            gated: bool = False,
    ) -> None:
        if not self._gate_policy.should_audit(tool_name):
            return
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            await uow.audit.add(AuditLog(
                actor_user_id=session.owner_user_id if session else None,
                action="agent_tool_invoke",
                resource_type="session",
                resource_id=self._session_id,
                team_id=session.team_id if session else None,
                metadata={
                    "tool": tool_name,
                    "args": redact_tool_args(arguments if isinstance(arguments, dict) else {}),
                    "success": result.success,
                    "execution_status": (
                        result.status.value if result.status is not None else None
                    ),
                    "attempts": [
                        attempt.model_dump(mode="json")
                        for attempt in result.attempts
                    ],
                    "result_summary": summarize_tool_result(result),
                    "duration_ms": duration_ms,
                    "gate_profile": self._runtime_settings.gate_profile,
                    "gated": gated,
                },
            ))
            await uow.commit()

    async def record_visited_domain(self, function_args: Dict[str, Any]) -> None:
        url = function_args.get("url") if isinstance(function_args, dict) else None
        domain = self._gate_policy.normalize_domain(str(url or ""))
        if not domain:
            return
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session:
                return
            meta = session.pending_metadata or {}
            visited = list(meta.get("visited_domains") or [])
            if domain in visited:
                return
            visited.append(domain)
            await uow.session.set_pending_metadata(
                self._session_id,
                merge_pending_metadata(meta, {"visited_domains": visited}),
            )
