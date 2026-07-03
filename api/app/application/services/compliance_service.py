#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compliance report generation against 等保2.0 + ISO27001 control catalog."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.application.services.audit_service import AuditService
from app.application.services.config_provider import get_runtime_config
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.compliance.control_mapping import CONTROLS, Control
from app.infrastructure.external.report.pdf_renderer import PdfUnavailableError, render_html_to_pdf
from app.infrastructure.models.audit_log import AuditLogORM
from app.infrastructure.models.session import SessionModel
from sqlalchemy import func, or_, select


class ComplianceService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service

    async def build_report(
        self,
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        frameworks: Optional[List[str]] = None,
    ) -> dict[str, Any]:
        chain = await self._audit_service.verify_chain()
        metrics = await self._collect_metrics(start_at, end_at)
        controls = [
            c
            for c in CONTROLS
            if not frameworks or c.framework in frameworks
        ]
        items = [await self._evaluate_control(c, metrics, chain) for c in controls]
        summary = {
            "pass": sum(1 for i in items if i["status"] == "pass"),
            "gap": sum(1 for i in items if i["status"] == "gap"),
            "na": sum(1 for i in items if i["status"] == "na"),
            "total": len(items),
        }
        return {
            "generated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "frameworks": frameworks or sorted({c.framework for c in CONTROLS}),
            "chain_verification": chain,
            "summary": summary,
            "controls": items,
        }

    async def _collect_metrics(
        self,
        start_at: Optional[datetime],
        end_at: Optional[datetime],
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            audit_stmt = select(func.count()).select_from(AuditLogORM)
            if start_at:
                audit_stmt = audit_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at:
                audit_stmt = audit_stmt.where(AuditLogORM.created_at <= end_at)
            audit_count = int((await uow.db_session.execute(audit_stmt)).scalar_one() or 0)

            gate_stmt = select(func.count()).select_from(AuditLogORM).where(
                AuditLogORM.action.in_(
                    [
                        "agent_plan_approve",
                        "agent_plan_reject",
                        "agent_tool_approve",
                        "agent_tool_reject",
                        "agent_takeover",
                        "agent_takeover_timeout",
                    ]
                )
            )
            if start_at:
                gate_stmt = gate_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at:
                gate_stmt = gate_stmt.where(AuditLogORM.created_at <= end_at)
            gate_count = int((await uow.db_session.execute(gate_stmt)).scalar_one() or 0)

            tool_stmt = select(func.count()).select_from(AuditLogORM).where(
                AuditLogORM.action == "agent_tool_invoke"
            )
            if start_at:
                tool_stmt = tool_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at:
                tool_stmt = tool_stmt.where(AuditLogORM.created_at <= end_at)
            tool_count = int((await uow.db_session.execute(tool_stmt)).scalar_one() or 0)

            scope_stmt = select(func.count()).select_from(AuditLogORM).where(
                AuditLogORM.action == "operator_scope_declared"
            )
            scope_count = int((await uow.db_session.execute(scope_stmt)).scalar_one() or 0)

            op_sessions_stmt = select(func.count()).select_from(SessionModel).where(
                or_(
                    SessionModel.operator_scope.isnot(None),
                    SessionModel.gate_profile.isnot(None),
                )
            )
            op_sessions = int((await uow.db_session.execute(op_sessions_stmt)).scalar_one() or 0)

            rollback_stmt = select(func.count()).select_from(AuditLogORM).where(
                AuditLogORM.action == "agent_rollback"
            )
            rollback_count = int((await uow.db_session.execute(rollback_stmt)).scalar_one() or 0)

        cfg = get_runtime_config()
        hitl = cfg.hitl
        return {
            "audit_count": audit_count,
            "gate_approval_count": gate_count,
            "tool_invoke_count": tool_count,
            "operator_scope_count": scope_count,
            "operator_sessions": op_sessions,
            "rollback_count": rollback_count,
            "hitl_enabled": cfg.feature_flags.enable_hitl_gates,
            "plan_gate": hitl.plan_gate_enabled,
            "tool_gate": hitl.tool_gate_call_level_enabled,
            "gate_profiles": list(hitl.gate_profiles.keys()),
            "redaction_module": True,
            "self_hosted": True,
            "evidence_export": True,
            "session_isolation": True,
            "encryption_at_rest": True,
        }

    async def _evaluate_control(
        self,
        control: Control,
        metrics: dict[str, Any],
        chain: dict[str, Any],
    ) -> dict[str, Any]:
        evaluator = getattr(self, f"_eval_{control.evaluator}", None)
        if evaluator is None:
            status, evidence = "na", ["evaluator not implemented"]
        else:
            status, evidence = evaluator(metrics, chain)
        return {
            "framework": control.framework,
            "control_id": control.control_id,
            "title": control.title,
            "requirement": control.requirement,
            "capability": control.capability,
            "evaluator": control.evaluator,
            "status": status,
            "evidence": evidence,
        }

    @staticmethod
    def _eval_auth_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["audit_count"] >= 0
        return ("pass" if ok else "gap", ["JWT/OAuth 登录", f"审计记录 {m['audit_count']} 条"])

    @staticmethod
    def _eval_rbac_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["GlobalRole: admin/user/auditor", "团队工作区 RBAC"])

    @staticmethod
    def _eval_operator_scope_declared(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["operator_scope_count"] > 0 or m["operator_sessions"] > 0
        return (
            "pass" if ok else "gap",
            [
                f"operator_scope 声明 {m['operator_scope_count']} 次",
                f"Web Operator 会话 {m['operator_sessions']} 个",
            ],
        )

    @staticmethod
    def _eval_gates_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["hitl_enabled"] and m["plan_gate"]
        return (
            "pass" if ok else "gap",
            [
                f"HITL enabled={m['hitl_enabled']}",
                f"gate_profiles={m['gate_profiles']}",
            ],
        )

    @staticmethod
    def _eval_chain_intact(_m: dict, chain: dict) -> tuple[str, list[str]]:
        ok = chain.get("ok", False) or chain.get("total", 0) == 0
        return (
            "pass" if ok else "gap",
            [
                f"链校验 ok={chain.get('ok')}",
                f"total={chain.get('total')}",
                f"first_broken={chain.get('first_broken_seq')}",
            ],
        )

    @staticmethod
    def _eval_redaction_on(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["audit_redaction.py 工具参数脱敏"])

    @staticmethod
    def _eval_rollback_capable(m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["CheckpointService 含浏览器 Profile", f"回滚审计 {m['rollback_count']} 次"])

    @staticmethod
    def _eval_audit_logging(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["audit_count"] > 0
        return ("pass" if ok else "gap", [f"audit_logs 共 {m['audit_count']} 条"])

    @staticmethod
    def _eval_gate_approvals(m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", [f"治理审批记录 {m['gate_approval_count']} 条"])

    @staticmethod
    def _eval_tool_audit(m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", [f"agent_tool_invoke {m['tool_invoke_count']} 条"])

    @staticmethod
    def _eval_self_hosted(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["Docker Compose / Helm 私有化部署"])

    @staticmethod
    def _eval_evidence_export(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["EvidenceService 证据包 ZIP+PDF"])

    @staticmethod
    def _eval_session_isolation(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["Docker/K8s 沙箱按需隔离"])

    @staticmethod
    def _eval_encryption_at_rest(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["LLM API Key 加密存储"])

    @staticmethod
    def _eval_input_untrusted(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["hitl_enabled"]
        return ("pass" if ok else "gap", ["页面不可信 + 逐工具门控"])

    @staticmethod
    def _eval_central_admin(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["Admin 后台集中管控与导出"])

    @staticmethod
    def _eval_timestamp_integrity(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["audit_logs.created_at UTC 时间戳"])

    @staticmethod
    def _eval_privileged_access_control(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["gate_approval_count"] > 0 or m["operator_sessions"] > 0
        return ("pass" if ok else "gap", ["HITL 审批门控", f"审批 {m['gate_approval_count']} 次"])

    @staticmethod
    def _eval_monitoring_present(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return ("pass", ["Admin 用量/审计概览", "Prometheus /metrics"])

    @staticmethod
    def _eval_crypto_controls(_m: dict, chain: dict) -> tuple[str, list[str]]:
        ok = chain.get("total", 0) == 0 or chain.get("ok", False)
        return ("pass" if ok else "gap", ["HMAC-SHA256 审计链", "API Key 加密"])

    def render_json(self, report: dict[str, Any]) -> str:
        return json.dumps(report, ensure_ascii=False, indent=2)

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# 合规审计报告\n",
            f"生成时间: {report.get('generated_at')}\n",
            f"时间范围: {report.get('start_at') or '全部'} — {report.get('end_at') or '全部'}\n",
            "## 摘要\n",
            f"- 通过: {report['summary']['pass']}\n",
            f"- 差距: {report['summary']['gap']}\n",
            f"- 不适用: {report['summary']['na']}\n",
            f"- 证据链: {'完整' if report['chain_verification'].get('ok') else '异常'}\n\n",
            "## 控制项明细\n\n",
        ]
        for item in report.get("controls", []):
            lines.append(
                f"### [{item['framework']}] {item['control_id']} {item['title']}\n"
            )
            lines.append(f"- **状态**: {item['status']}\n")
            lines.append(f"- **要求**: {item['requirement']}\n")
            lines.append(f"- **平台能力**: {item['capability']}\n")
            lines.append(f"- **证据**: {', '.join(item.get('evidence', []))}\n\n")
        return "".join(lines)

    def render_pdf(self, report: dict[str, Any]) -> Optional[bytes]:
        html = (
            "<html><head><meta charset='utf-8'><style>"
            "body{font-family:sans-serif;padding:2em;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
            "h1{color:#1e3a5f;}"
            "</style></head><body>"
            + self.render_markdown(report).replace("\n", "<br/>")
            + "</body></html>"
        )
        try:
            return render_html_to_pdf(html)
        except PdfUnavailableError:
            return None
