#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工具 HITL 门禁的纯判定逻辑。

从 ``BaseAgent`` 中抽出：判断某次工具调用是否需要审计 / 是否命中 gate 规则 /
是否需要人工审批。除审批流程里对 session 的只读查询外不做任何写操作
（写审计日志、写 pending_metadata 属于 ``tool_audit_recorder.ToolAuditRecorder``
的职责）。行为与原 ``BaseAgent`` 私有方法完全一致，仅搬迁位置。
"""
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from app.domain.config_port import get_runtime_config
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.tool_policy import ApprovalMode
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.tool_batch_executor import PreparedToolCall
from app.domain.services.tools.tool_names import is_tool_allowed
from app.domain.utils.hitl import (
    domain_in_whitelist,
    matches_critical_action,
    resolve_gate_profile_settings,
    tool_matches_risk_list,
)


class ToolGatePolicy:
    """封装工具审批/审计相关的门禁判定，行为与原 BaseAgent 私有方法一致。"""

    def __init__(
            self,
            *,
            runtime_settings: AgentRuntimeSettings,
            session_id: str,
            uow_factory: Callable[[], IUnitOfWork],
            allowed_tool_names: Optional[list] = None,
    ) -> None:
        # 不变量：本类持有构造时值快照；BaseAgent 对应字段构造后不得重绑定，
        # 否则审计/gate 将静默使用旧值。
        self._runtime_settings = runtime_settings
        self._session_id = session_id
        self._uow_factory = uow_factory
        self._allowed_tool_names = allowed_tool_names

    @staticmethod
    def normalize_domain(url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return (parsed.hostname or "").lower()

    def effective_gate_profile(self) -> str:
        return (self._runtime_settings.gate_profile or "standard").lower()

    def gate_profile_settings(self):
        runtime = get_runtime_config()
        return resolve_gate_profile_settings(self.effective_gate_profile(), runtime.hitl)

    def tool_gate_call_level_enabled(self) -> bool:
        runtime = get_runtime_config()
        if not runtime.feature_flags.enable_hitl_gates:
            return False
        override = self._runtime_settings.tool_gate_call_level_enabled
        if override is not None:
            return bool(override)
        if self._runtime_settings.gate_profile:
            return bool(self.gate_profile_settings().tool_gate_call_level_enabled)
        return runtime.hitl.tool_gate_call_level_enabled

    def should_audit(self, tool_name: str) -> bool:
        if not self._runtime_settings.gate_profile:
            return False
        lowered = tool_name.lower()
        if lowered.startswith("browser_"):
            return True
        if lowered in {"shell_execute", "a2a"}:
            return True
        if lowered.startswith("mcp_") or tool_name == "mcp":
            return True
        return False

    def compute_gated_flag(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """Whether this tool call would match per-call gate rules (risk list / critical action)."""
        if not self._runtime_settings.gate_profile:
            return False
        runtime = get_runtime_config()
        if not tool_matches_risk_list(tool_name, runtime.hitl.tool_gate_risk_list):
            return False
        profile_settings = self.gate_profile_settings()
        if profile_settings.selective_critical_only:
            return matches_critical_action(
                tool_name,
                arguments,
                runtime.hitl.critical_action_patterns,
            )
        return self.tool_gate_call_level_enabled()

    async def authorizes_tool(self, call: PreparedToolCall) -> bool:
        return (
            self._allowed_tool_names is None
            or is_tool_allowed(call.function_name, self._allowed_tool_names)
        )

    async def requires_policy_approval(self, call: PreparedToolCall) -> bool:
        if call.policy.approval != ApprovalMode.POLICY:
            return call.policy.approval == ApprovalMode.ALWAYS
        runtime = get_runtime_config()
        function_name = call.function_name
        function_args = call.normalized_args

        first_visit_domain = None
        if function_name == "browser_navigate":
            url = function_args.get("url")
            domain = self.normalize_domain(str(url or ""))
            if domain:
                first_visit_domain = domain

        risk_gated = (
            self.tool_gate_call_level_enabled()
            and tool_matches_risk_list(
                function_name,
                runtime.hitl.tool_gate_risk_list,
            )
        )
        profile_settings = (
            self.gate_profile_settings()
            if self._runtime_settings.gate_profile
            else None
        )
        if (
            risk_gated
            and profile_settings
            and profile_settings.selective_critical_only
            and not matches_critical_action(
                function_name,
                function_args,
                runtime.hitl.critical_action_patterns,
            )
        ):
            risk_gated = False

        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
            if not session or not getattr(session, "operator_scope", None):
                return False
            meta = session.pending_metadata or {}
            if risk_gated:
                approved = meta.get("approved_tools") or []
                if any(
                    tool_matches_risk_list(function_name, [item])
                    for item in approved
                ):
                    risk_gated = False
            if first_visit_domain:
                whitelist = list(
                    self._runtime_settings.operator_domains
                    or getattr(session, "operator_domains", None)
                    or []
                )
                already_allowed = (
                    domain_in_whitelist(first_visit_domain, whitelist)
                    or first_visit_domain
                    in set(meta.get("visited_domains") or [])
                    or first_visit_domain
                    in set(meta.get("approved_domains") or [])
                )
                if not already_allowed:
                    return True
        return risk_gated
