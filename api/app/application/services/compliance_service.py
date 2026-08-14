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
from app.domain.utils.audit_redaction import scrub_secret_patterns
from app.infrastructure.external.report.pdf_renderer import PdfUnavailableError, render_html_to_pdf
from app.infrastructure.models.audit_log import AuditLogORM
from app.infrastructure.models.session import SessionModel
from core.config import Settings, get_settings
from sqlalchemy import func, or_, select

# Real audit `action` values that correspond to a user login/logout event, if
# such a value is ever recorded. As of this module's last audit (grep over
# app/interfaces/endpoints/auth_routes.py and app/application/services/
# auth_service.py) no login/logout event is written to the audit chain at
# all -- only `users.last_login_at` is updated. auth_event_count is therefore
# expected to be 0 today; that is a real gap this evaluator should surface
# honestly (attention), not paper over with a fake pass.
LOGIN_AUDIT_ACTIONS: tuple[str, ...] = ("login", "logout", "oauth_login")

# Real audit action written on session evidence package export. Verified via
# grep for `action="patrol_evidence_downloaded"` in
# app/application/services/patrol_evidence_service.py; the general session
# evidence ZIP download (EvidenceService.build_session_evidence_package /
# compliance_routes.download_evidence_package) does not currently write an
# audit record.
EVIDENCE_EXPORT_AUDIT_ACTION = "patrol_evidence_downloaded"

# Real audit action prefix for admin-only mutations. Verified via grep for
# `action="admin.` across app/interfaces/endpoints/admin_routes.py.
ADMIN_AUDIT_ACTION_PREFIX = "admin."

# Known public-cloud/SaaS LLM API hosts. This is a **denylist, not a
# whitelist, and not exhaustive** -- an endpoint host matching one of these
# means the platform is *known* to not be fully self-hosted for that model
# traffic; a host *not* matching is not proof of self-hosting (it could be
# an unlisted SaaS provider). _eval_self_hosted's evidence string says so
# explicitly rather than implying a complete allowlist was checked.
PUBLIC_SAAS_LLM_HOSTS: frozenset[str] = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.cohere.ai",
        "api.mistral.ai",
        "api.groq.com",
        "api.together.xyz",
        "openrouter.ai",
        "api.perplexity.ai",
        "dashscope.aliyuncs.com",
        "api.deepseek.com",
        "open.bigmodel.cn",
        "api.moonshot.cn",
    }
)

# Suffix-matched public-cloud hosts: these providers serve LLM APIs off
# tenant/region-specific subdomains (Azure OpenAI resource names, AWS/GCP
# regions), so an exact-match set would silently miss most real
# configurations. Matched via host.endswith(suffix); for the AWS/GCP
# entries a keyword must also appear in the host so we flag the LLM
# services specifically (Bedrock / Vertex aiplatform) rather than every
# amazonaws.com/googleapis.com host (e.g. S3, unrelated GCP APIs).
_PUBLIC_SAAS_LLM_HOST_SUFFIXES: tuple[str, ...] = (
    "openai.azure.com",  # Azure OpenAI: <resource>.openai.azure.com
)
_PUBLIC_SAAS_LLM_HOST_SUFFIX_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("amazonaws.com", "bedrock"),  # AWS Bedrock: bedrock-runtime.<region>.amazonaws.com
    ("googleapis.com", "aiplatform"),  # GCP Vertex AI: <region>-aiplatform.googleapis.com
)


def _is_known_public_llm_host(host: str) -> bool:
    if host in PUBLIC_SAAS_LLM_HOSTS:
        return True
    if any(host == s or host.endswith(f".{s}") for s in _PUBLIC_SAAS_LLM_HOST_SUFFIXES):
        return True
    for suffix, keyword in _PUBLIC_SAAS_LLM_HOST_SUFFIX_KEYWORDS:
        if (host == suffix or host.endswith(f".{suffix}")) and keyword in host:
            return True
    return False


def _metadata_is_secret_free(metadata: Optional[Dict[str, Any]]) -> bool:
    """Free-text secret spot check for one audit entry's metadata.

    Key-based redaction (``sanitize_audit_metadata`` in audit_service.py)
    already runs on every write; this re-scans the *rendered* metadata with
    ``scrub_secret_patterns`` (regex-based, catches secrets embedded in
    free-text fields that key matching cannot see) as an independent,
    dynamic confirmation rather than trusting the write path blindly.
    """
    rendered = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, default=str)
    return scrub_secret_patterns(rendered) == rendered


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
            "attention": sum(1 for i in items if i["status"] == "attention"),
            "not_verified": sum(1 for i in items if i["status"] == "not_verified"),
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

            # --- New inputs (Task 4): every query below goes through a
            # repository method (uow.<repo>.<method>) rather than a raw
            # `uow.db_session.execute` call -- see db_*_repository.py for
            # the concrete implementations and the corresponding abstract
            # method on the *_repository.py protocol/ABC. ---
            auth_event_count = await uow.audit.count_by_actions(
                list(LOGIN_AUDIT_ACTIONS), start_at=start_at, end_at=end_at
            )
            role_distribution = await uow.user.count_by_role()
            agent_session_count = await uow.session.count_created_between(start_at, end_at)
            checkpoint_window_count = await uow.checkpoint.count_created_between(start_at, end_at)
            llm_endpoint_hosts = await uow.llm_endpoint.list_hosts()
            evidence_export_count = await uow.audit.count(
                action=EVIDENCE_EXPORT_AUDIT_ACTION, start_at=start_at, end_at=end_at
            )
            admin_action_count = await uow.audit.count_by_action_prefix(
                ADMIN_AUDIT_ACTION_PREFIX, start_at=start_at, end_at=end_at
            )
            redaction_sample_logs = await uow.audit.list(
                action="agent_tool_invoke",
                start_at=start_at,
                end_at=end_at,
                limit=20,
            )
            # Distinct from redaction_sample_logs: ordered by chain_seq (the
            # write-order hash-chain sequence), not by created_at, so a
            # "is created_at monotonic here" check is non-tautological. See
            # AuditRepository.list_recent_chained docstring.
            timestamp_chain_logs = await uow.audit.list_recent_chained(limit=20)

        cfg = get_runtime_config()
        hitl = cfg.hitl
        settings = get_settings()
        default_signing_key = Settings.model_fields["audit_signing_key"].default
        redaction_spot_check = [
            {
                "created_at": log.created_at,
                "clean": _metadata_is_secret_free(log.metadata),
            }
            for log in redaction_sample_logs
        ]
        # Already ascending chain_seq order courtesy of list_recent_chained;
        # _eval_timestamp_integrity checks created_at monotonicity directly
        # against this order, no re-sorting needed (and re-sorting by
        # created_at here would defeat the point).
        timestamp_chain_sample = [
            {"created_at": log.created_at, "chain_seq": log.chain_seq}
            for log in timestamp_chain_logs
        ]
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
            "auth_event_count": auth_event_count,
            "role_distribution": role_distribution,
            "agent_session_count": agent_session_count,
            "checkpoint_count": checkpoint_window_count,
            "llm_endpoint_hosts": llm_endpoint_hosts,
            "evidence_export_count": evidence_export_count,
            "admin_action_count": admin_action_count,
            "redaction_spot_check": redaction_spot_check,
            "timestamp_chain_sample": timestamp_chain_sample,
            "sandbox_driver": cfg.sandbox.driver,
            "metrics_token_configured": bool(settings.metrics_token),
            "audit_signing_key_id": settings.audit_signing_key_id,
            "signing_key_is_default": settings.audit_signing_key == default_signing_key,
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
        count = m.get("auth_event_count", 0)
        if count > 0:
            return ("pass", [f"登录类审计动作 {count} 条（action ∈ {list(LOGIN_AUDIT_ACTIONS)}）"])
        return (
            "attention",
            [
                f"窗口内登录类审计动作 0 条（action ∈ {list(LOGIN_AUDIT_ACTIONS)}）",
                "登录/登出当前未写入不可篡改审计链，仅 users.last_login_at 记录",
            ],
        )

    @staticmethod
    def _eval_rbac_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        dist: Dict[str, int] = m.get("role_distribution", {}) or {}
        admin_count = dist.get("admin", 0)
        non_admin_count = sum(count for role, count in dist.items() if role != "admin")
        if admin_count > 0 and non_admin_count > 0:
            return ("pass", [f"角色分布: {dist}"])
        return ("attention", [f"角色分布: {dist}（未形成 admin/非 admin 实际分层）"])

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
    def _eval_redaction_on(m: dict, _c: dict) -> tuple[str, list[str]]:
        sample: List[dict] = m.get("redaction_spot_check", []) or []
        if not sample:
            return (
                "attention",
                [
                    "audit_redaction.py 键名脱敏写路径存在（静态事实）",
                    "窗口内无 agent_tool_invoke 审计样本可供动态抽检",
                ],
            )
        dirty = [s for s in sample if not s.get("clean", True)]
        if dirty:
            return (
                "attention",
                [f"抽样 {len(sample)} 条 agent_tool_invoke 元数据，{len(dirty)} 条疑似残留明文密钥模式"],
            )
        return (
            "pass",
            [
                "audit_redaction.py 键名脱敏写路径 + scrub_secret_patterns 抽检",
                f"抽样最近 {len(sample)} 条 agent_tool_invoke 元数据均未见明文密钥模式",
            ],
        )

    @staticmethod
    def _eval_rollback_capable(m: dict, _c: dict) -> tuple[str, list[str]]:
        count = m.get("checkpoint_count", 0)
        if count > 0:
            return ("pass", ["CheckpointService 含浏览器 Profile", f"窗口内检查点创建 {count} 次"])
        return ("attention", ["CheckpointService 具备回滚能力，但窗口内检查点创建 0 次（能力未被演练）"])

    @staticmethod
    def _eval_audit_logging(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["audit_count"] > 0
        return ("pass" if ok else "gap", [f"audit_logs 共 {m['audit_count']} 条"])

    @staticmethod
    def _eval_gate_approvals(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m["gate_approval_count"] > 0:
            return ("pass", [f"治理审批记录 {m['gate_approval_count']} 条"])
        if m.get("agent_session_count", 0) > 0:
            return (
                "attention",
                [f"窗口内 Agent 会话 {m['agent_session_count']} 个，但审批记录 0 条：门控未被触发"],
            )
        return ("not_verified", ["窗口内无 Agent 会话，无法验证审批门控是否被触发"])

    @staticmethod
    def _eval_tool_audit(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m["tool_invoke_count"] > 0:
            return ("pass", [f"agent_tool_invoke {m['tool_invoke_count']} 条"])
        if m.get("agent_session_count", 0) > 0:
            return (
                "attention",
                [f"窗口内 Agent 会话 {m['agent_session_count']} 个，但工具调用审计 0 条"],
            )
        return ("not_verified", ["窗口内无 Agent 会话，无法验证工具调用审计是否被触发"])

    @staticmethod
    def _eval_self_hosted(m: dict, _c: dict) -> tuple[str, list[str]]:
        hosts: List[str] = m.get("llm_endpoint_hosts", []) or []
        if not hosts:
            return ("not_verified", ["未配置任何 LLM Endpoint，无法验证私有化部署边界"])
        public = sorted({h for h in hosts if _is_known_public_llm_host(h)})
        if public:
            return ("attention", ["Docker Compose / Helm 私有化部署", f"LLM Endpoint 含已知公网 LLM SaaS 域: {public}"])
        return (
            "pass",
            [
                "Docker Compose / Helm 私有化部署",
                f"LLM Endpoint host 共 {len(hosts)} 个，均未命中已知公网 LLM SaaS 域名单（denylist，不完备，未命中不等于已证实自托管）",
            ],
        )

    @staticmethod
    def _eval_evidence_export(m: dict, _c: dict) -> tuple[str, list[str]]:
        count = m.get("evidence_export_count", 0)
        if count > 0:
            return ("pass", [f"证据导出审计 {EVIDENCE_EXPORT_AUDIT_ACTION} {count} 条"])
        return ("attention", [f"窗口内证据导出审计 {EVIDENCE_EXPORT_AUDIT_ACTION} 0 条"])

    @staticmethod
    def _eval_session_isolation(m: dict, _c: dict) -> tuple[str, list[str]]:
        driver = (m.get("sandbox_driver") or "").strip()
        if driver:
            return ("pass", [f"沙箱驱动配置: driver={driver}"])
        return ("gap", ["沙箱驱动未配置（sandbox.driver 为空），无法确认隔离执行"])

    @staticmethod
    def _eval_encryption_at_rest(_m: dict, _c: dict) -> tuple[str, list[str]]:
        return (
            "not_verified",
            ["应用层无法验证磁盘加密，需部署层佐证；API Key 应用层加密属实"],
        )

    @staticmethod
    def _eval_input_untrusted(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["hitl_enabled"]
        return ("pass" if ok else "gap", ["页面不可信 + 逐工具门控"])

    @staticmethod
    def _eval_central_admin(m: dict, _c: dict) -> tuple[str, list[str]]:
        count = m.get("admin_action_count", 0)
        if count > 0:
            return ("pass", ["Admin 后台集中管控与导出", f"{ADMIN_AUDIT_ACTION_PREFIX}* 审计动作 {count} 条"])
        return ("attention", [f"窗口内 {ADMIN_AUDIT_ACTION_PREFIX}* 审计动作 0 条"])

    @staticmethod
    def _eval_timestamp_integrity(m: dict, _c: dict) -> tuple[str, list[str]]:
        # timestamp_chain_sample is ordered ascending by chain_seq (the
        # write-order hash-chain sequence -- see AuditRepository.
        # list_recent_chained), NOT by created_at. Checking created_at
        # monotonicity *along chain order* is what makes this a real check:
        # chain_seq is assigned independently of created_at, so agreement
        # between the two is an actual property of the data, not an
        # artifact of how the sample was sorted. This is the primary
        # judgment and holds regardless of tz-awareness -- naive datetimes
        # are still totally ordered and comparable.
        #
        # tz-awareness is a secondary, best-effort signal only:
        # AuditLogORM.created_at is `DateTime` *without* `timezone=True`
        # (see app/infrastructure/models/audit_log.py), so every row read
        # back from a real deployment's DB is a naive datetime. Treating
        # that as an unconditional gap would make this evaluator report a
        # permanent false gap in production despite the data actually
        # being chain-order consistent. Naive datetimes are therefore
        # treated as UTC-by-platform-convention: if the chain-seq-order
        # monotonicity check passes, a naive sample degrades to
        # `attention` (real, disclosed limitation) rather than `pass`
        # (would overclaim tz-aware verification) or `gap` (would
        # misrepresent a schema limitation as a data integrity failure).
        sample: List[dict] = m.get("timestamp_chain_sample", []) or []
        timestamps = [s["created_at"] for s in sample if s.get("created_at") is not None]
        if not timestamps:
            return ("not_verified", ["窗口内无已入链审计样本，时间戳完整性检查无数据可判"])
        try:
            monotonic = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
        except TypeError:
            # Mixed naive/aware datetimes in the same sample are not
            # comparable -- that inconsistency is itself an integrity gap.
            return ("gap", ["抽样中 created_at 时区感知（aware/naive）不一致，无法比较链序单调性"])
        if not monotonic:
            return (
                "gap",
                [f"抽样 {len(timestamps)} 条按 chain_seq 链序排列的审计条目，created_at 沿链序非单调递增，链序与时间戳不一致"],
            )
        all_aware = all(getattr(ts, "tzinfo", None) is not None for ts in timestamps)
        if all_aware:
            return (
                "pass",
                [f"抽样 {len(timestamps)} 条按 chain_seq 链序排列的审计条目，created_at 均为 UTC aware 且沿链序单调不减"],
            )
        return (
            "attention",
            [
                f"抽样 {len(timestamps)} 条按 chain_seq 链序排列的审计条目，created_at 沿链序单调不减",
                "audit 表 created_at 为 TIMESTAMP WITHOUT TIME ZONE，应用层无法断言 tz-aware；建议 schema 加 timezone=True（backlog）",
            ],
        )

    @staticmethod
    def _eval_privileged_access_control(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["gate_approval_count"] > 0 or m["operator_sessions"] > 0
        return ("pass" if ok else "gap", ["HITL 审批门控", f"审批 {m['gate_approval_count']} 次"])

    @staticmethod
    def _eval_monitoring_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m.get("metrics_token_configured"):
            return (
                "pass",
                [
                    "governance_* 指标族（governance_metrics.py）已接入",
                    "metrics_token 已配置",
                    "Admin 治理 Dashboard（Task 5）",
                ],
            )
        return (
            "attention",
            [
                "governance_* 指标族（governance_metrics.py）已接入",
                "metrics_token 未配置，/metrics 端点当前 fail-closed 关闭",
            ],
        )

    @staticmethod
    def _eval_crypto_controls(m: dict, chain: dict) -> tuple[str, list[str]]:
        ok = chain.get("total", 0) == 0 or chain.get("ok", False)
        evidence = [
            f"HMAC-SHA256 审计链（key_id={m.get('audit_signing_key_id', '')}）",
            "API Key 加密",
        ]
        if not ok:
            return ("gap", evidence)
        if m.get("signing_key_is_default"):
            return ("attention", evidence + ["audit_signing_key 仍为出厂默认值，需在生产环境轮换"])
        return ("pass", evidence)

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# 合规审计报告\n",
            f"生成时间: {report.get('generated_at')}\n",
            f"时间范围: {report.get('start_at') or '全部'} — {report.get('end_at') or '全部'}\n",
            "## 摘要\n",
            f"- 通过: {report['summary']['pass']}\n",
            f"- 差距: {report['summary']['gap']}\n",
            f"- 关注: {report['summary'].get('attention', 0)}\n",
            f"- 待人工核实: {report['summary'].get('not_verified', 0)}\n",
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
