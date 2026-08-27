"""Compliance report generation against 等保2.0 + ISO27001 control catalog."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.application.ports.queries import (
    ComplianceEvidenceQueryPort,
    RunProjectionPort,
)
from app.application.ports.reporting import ComplianceRuntimeValues, ReportRendererPort
from app.application.services.audit_service import AuditService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.runtime_policy import RuntimePolicyUnavailableError
from app.domain.services.compliance.control_mapping import CONTROLS, Control
from app.domain.utils.audit_redaction import scrub_secret_patterns

# Real audit `action` values that correspond to a user login/logout event, if
# such a value is ever recorded. As of this module's last audit (grep over
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
    (
        "googleapis.com",
        "aiplatform",
    ),  # GCP Vertex AI: <region>-aiplatform.googleapis.com
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


def _metadata_is_secret_free(metadata: dict[str, Any] | None) -> bool:
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
        evidence_query: ComplianceEvidenceQueryPort,
        audit_service: AuditService,
        run_projection: RunProjectionPort,
        runtime_values: ComplianceRuntimeValues,
        policy_heads: PolicyHeadReader,
        report_renderer: ReportRendererPort,
    ) -> None:
        self._evidence_query = evidence_query
        self._audit_service = audit_service
        self._runs = run_projection
        self._runtime_values = runtime_values
        self._policy_heads = policy_heads
        self._report_renderer = report_renderer

    async def build_report(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        frameworks: list[str] | None = None,
    ) -> dict[str, Any]:
        chain = await self._audit_service.verify_chain()
        metrics = await self._collect_metrics(start_at, end_at)
        controls = [c for c in CONTROLS if not frameworks or c.framework in frameworks]
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
            "generated_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "frameworks": frameworks or sorted({c.framework for c in CONTROLS}),
            "chain_verification": chain,
            "runtime_policy": metrics["runtime_policy"],
            "summary": summary,
            "controls": items,
        }

    async def _collect_metrics(
        self,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict[str, Any]:
        evidence = await self._evidence_query.collect(
            start_at=start_at,
            end_at=end_at,
        )
        execution_metrics = await self._runs.execution_metrics(
            start_at=start_at,
            end_at=end_at,
        )
        now = datetime.now(UTC)
        execution_active = await self._policy_heads.active_execution(
            require_fresh=False,
            now=now,
        )
        operations_active = await self._policy_heads.active_operations(
            require_fresh=False,
            now=now,
        )
        if execution_active.head != operations_active.head:
            raise RuntimePolicyUnavailableError(
                "Runtime Policy head changed while collecting compliance evidence"
            )
        runtime_values = self._runtime_values
        redaction_spot_check = [
            {
                "created_at": log.created_at,
                "clean": _metadata_is_secret_free(log.metadata),
            }
            for log in evidence.redaction_sample_logs
        ]
        # Already ascending chain_seq order courtesy of list_recent_chained;
        # _eval_timestamp_integrity checks created_at monotonicity directly
        # against this order, no re-sorting needed (and re-sorting by
        # created_at here would defeat the point).
        timestamp_chain_sample = [
            {"created_at": log.created_at, "chain_seq": log.chain_seq}
            for log in evidence.timestamp_chain_logs
        ]
        return {
            "audit_count": evidence.audit_count,
            **execution_metrics,
            "operator_scope_count": evidence.operator_scope_count,
            "operator_sessions": evidence.operator_sessions,
            "auth_event_count": evidence.auth_event_count,
            "role_distribution": evidence.role_distribution,
            "inference_endpoint_hosts": list(evidence.inference_endpoint_hosts),
            "evidence_export_count": evidence.evidence_export_count,
            "admin_action_count": evidence.admin_action_count,
            "redaction_spot_check": redaction_spot_check,
            "timestamp_chain_sample": timestamp_chain_sample,
            "sandbox_driver": runtime_values.sandbox_driver,
            "runtime_policy": {
                "head_version": execution_active.head.version,
                "execution_revision_id": str(execution_active.revision.id),
                "execution_digest": execution_active.revision.digest,
                "operations_revision_id": str(operations_active.revision.id),
                "operations_digest": operations_active.revision.digest,
            },
            "metrics_token_configured": runtime_values.metrics_token_configured,
            "audit_signing_key_id": runtime_values.audit_signing_key_id,
            "signing_key_is_default": runtime_values.signing_key_is_default,
        }

    async def _evaluate_control(
        self,
        control: Control,
        metrics: dict[str, Any],
        chain: dict[str, Any],
    ) -> dict[str, Any]:
        evaluator = getattr(self, f"_eval_{control.evaluator}", None)
        if evaluator is None:
            raise RuntimeError(f"compliance evaluator is not registered: {control.evaluator}")
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
            return (
                "pass",
                [f"登录类审计动作 {count} 条（action ∈ {list(LOGIN_AUDIT_ACTIONS)}）"],
            )
        return ("not_verified", ["窗口内没有登录类审计动作"])

    @staticmethod
    def _eval_rbac_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        dist: dict[str, int] = m.get("role_distribution", {}) or {}
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
    def _eval_approvals_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        approvals = m["approval_request_count"]
        runs = m["run_count"]
        if approvals > 0:
            return ("pass", [f"正式 ApprovalRequested 事实 {approvals} 条"])
        if runs > 0:
            return ("attention", [f"窗口内 Run {runs} 个，未触发人工审批"])
        return ("not_verified", ["窗口内无 Run，无法动态验证正式审批"])

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
        sample: list[dict] = m.get("redaction_spot_check", []) or []
        if not sample:
            return (
                "attention",
                [
                    "audit_redaction.py 键名脱敏写路径存在（静态事实）",
                    "窗口内无审计元数据样本可供动态抽检",
                ],
            )
        dirty = [s for s in sample if not s.get("clean", True)]
        if dirty:
            return (
                "attention",
                [f"抽样 {len(sample)} 条审计元数据，{len(dirty)} 条疑似残留明文密钥模式"],
            )
        return (
            "pass",
            [
                "audit_redaction.py 键名脱敏写路径 + scrub_secret_patterns 抽检",
                f"抽样最近 {len(sample)} 条审计元数据均未见明文密钥模式",
            ],
        )

    @staticmethod
    def _eval_audit_logging(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["audit_count"] > 0
        return ("pass" if ok else "gap", [f"audit_logs 共 {m['audit_count']} 条"])

    @staticmethod
    def _eval_approval_coverage(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m["approval_request_count"] > 0:
            return ("pass", [f"正式审批请求 {m['approval_request_count']} 条"])
        if m.get("run_count", 0) > 0:
            return (
                "attention",
                [f"窗口内 Run {m['run_count']} 个，但正式审批请求为 0 条"],
            )
        return ("not_verified", ["窗口内无 Run，无法验证正式审批"])

    @staticmethod
    def _eval_tool_audit(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m["tool_activity_count"] > 0:
            return ("pass", [f"正式 tool.call Activity {m['tool_activity_count']} 条"])
        if m.get("run_count", 0) > 0:
            return (
                "attention",
                [f"窗口内 Run {m['run_count']} 个，但 tool.call Activity 0 条"],
            )
        return ("not_verified", ["窗口内无 Run，无法验证工具 Activity 记录"])

    @staticmethod
    def _eval_self_hosted(m: dict, _c: dict) -> tuple[str, list[str]]:
        hosts: list[str] = m.get("inference_endpoint_hosts", []) or []
        if not hosts:
            return ("not_verified", ["未配置任何推理端点，无法验证私有化部署边界"])
        public = sorted({h for h in hosts if _is_known_public_llm_host(h)})
        if public:
            return (
                "attention",
                [
                    "Docker Compose / Helm 私有化部署",
                    f"推理端点含已知公网推理 SaaS 域: {public}",
                ],
            )
        return (
            "pass",
            [
                "Docker Compose / Helm 私有化部署",
                f"推理端点 host 共 {len(hosts)} 个，均未命中已知公网推理 SaaS 域名单（denylist，不完备，未命中不等于已证实自托管）",
            ],
        )

    @staticmethod
    def _eval_evidence_export(m: dict, _c: dict) -> tuple[str, list[str]]:
        count = m.get("evidence_export_count", 0)
        if count > 0:
            return ("pass", [f"证据导出审计 {EVIDENCE_EXPORT_AUDIT_ACTION} {count} 条"])
        return (
            "attention",
            [f"窗口内证据导出审计 {EVIDENCE_EXPORT_AUDIT_ACTION} 0 条"],
        )

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
        del m
        return ("pass", ["外部页面内容标记为不可信，效果性工具使用正式审批"])

    @staticmethod
    def _eval_central_admin(m: dict, _c: dict) -> tuple[str, list[str]]:
        count = m.get("admin_action_count", 0)
        if count > 0:
            return (
                "pass",
                [
                    "Admin 后台集中管控与导出",
                    f"{ADMIN_AUDIT_ACTION_PREFIX}* 审计动作 {count} 条",
                ],
            )
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
        sample: list[dict] = m.get("timestamp_chain_sample", []) or []
        timestamps = [s["created_at"] for s in sample if s.get("created_at") is not None]
        if not timestamps:
            return (
                "not_verified",
                ["窗口内无已入链审计样本，时间戳完整性检查无数据可判"],
            )
        try:
            monotonic = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
        except TypeError:
            # Mixed naive/aware datetimes in the same sample are not
            # comparable -- that inconsistency is itself an integrity gap.
            return (
                "gap",
                ["抽样中 created_at 时区感知（aware/naive）不一致，无法比较链序单调性"],
            )
        if not monotonic:
            return (
                "gap",
                [
                    f"抽样 {len(timestamps)} 条按 chain_seq 链序排列的审计条目，created_at 沿链序非单调递增，链序与时间戳不一致"
                ],
            )
        if not all(getattr(ts, "tzinfo", None) is not None for ts in timestamps):
            return ("gap", ["审计 created_at 必须全部带时区"])
        return (
            "pass",
            [f"抽样 {len(timestamps)} 条审计记录均为 aware 且沿链序单调不减"],
        )

    @staticmethod
    def _eval_privileged_access_control(m: dict, _c: dict) -> tuple[str, list[str]]:
        ok = m["approval_request_count"] > 0 or m["operator_sessions"] > 0
        return (
            "pass" if ok else "gap",
            ["正式审批门控", f"审批请求 {m['approval_request_count']} 次"],
        )

    @staticmethod
    def _eval_monitoring_present(m: dict, _c: dict) -> tuple[str, list[str]]:
        if m.get("metrics_token_configured"):
            return (
                "pass",
                [
                    "governance_* 指标族（governance_metrics.py）已接入",
                    "metrics_token 已配置",
                    "Admin 治理 Dashboard",
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
            return (
                "attention",
                [*evidence, "audit_signing_key 仍为出厂默认值，需在生产环境轮换"],
            )
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
            lines.append(f"### [{item['framework']}] {item['control_id']} {item['title']}\n")
            lines.append(f"- **状态**: {item['status']}\n")
            lines.append(f"- **要求**: {item['requirement']}\n")
            lines.append(f"- **平台能力**: {item['capability']}\n")
            lines.append(f"- **证据**: {', '.join(item.get('evidence', []))}\n\n")
        return "".join(lines)

    def render_pdf(self, report: dict[str, Any]) -> bytes | None:
        return self._report_renderer.render_pdf(
            markdown=self.render_markdown(report),
            title="合规审计报告",
        )
