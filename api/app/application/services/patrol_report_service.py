"""Deterministic Markdown rendering for structured Patrol truth."""
from __future__ import annotations

import json
import re
from typing import Any

from app.domain.models.patrol import PatrolCheckResult, PatrolFinding, PatrolPackConfig, PatrolRun
from app.domain.utils.audit_redaction import redact_value


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(password|passwd|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+(@)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


def _text(value: Any) -> str:
    rendered = str(value)
    rendered = _SECRET_PATTERNS[0].sub(r"\1***REDACTED***", rendered)
    rendered = _SECRET_PATTERNS[1].sub(r"\1=***REDACTED***", rendered)
    rendered = _SECRET_PATTERNS[2].sub(r"\1***REDACTED***\2", rendered)
    rendered = _SECRET_PATTERNS[3].sub("***REDACTED***", rendered)
    return rendered


def _json(value: Any) -> str:
    safe = redact_value("", value)
    return _text(json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


class PatrolReportService:
    """Render only persisted fields; no LLM content can change report truth."""

    @staticmethod
    def render(
        run: PatrolRun,
        results: list[PatrolCheckResult],
        findings: list[PatrolFinding],
        *,
        previous_run: PatrolRun | None = None,
    ) -> str:
        config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
        by_id = {item.check_id: item for item in results}
        finding_by_result = {item.check_result_id: item for item in findings}
        lines = [
            f"# {_text(run.pack_snapshot.get('name', run.pack_id))} 巡检报告",
            "",
            "## 执行摘要",
            "",
            f"- Run: `{run.id}`",
            f"- 状态: **{run.status.value}**",
            f"- Pack Version: `{run.pack_version}`",
            f"- 目标: `{_text(config.target_ref)}`",
            f"- 结果: PASS {run.pass_count} / WARN {run.warn_count} / FAIL {run.fail_count} / ERROR {run.error_count} / SKIPPED {run.skipped_count}",
            f"- 证据完整率: `{(run.evidence_completeness or 0):.2%}`",
            "",
            "## 需要处理",
            "",
        ]
        actionable = [item for item in findings if item.status.value in {"open", "acknowledged"}]
        if actionable:
            for item in sorted(actionable, key=lambda value: (value.severity.value, value.fingerprint), reverse=True):
                lines.append(f"- **{item.severity.value.upper()}** {_text(item.title)} — {_text(item.summary)} (`{item.id}`)")
        else:
            lines.append("- 无开放 Finding。")

        lines.extend(["", "## 全部检查结果", ""])
        for check in config.checks:
            result = by_id.get(check.id)
            if result is None:
                lines.extend([f"### {check.title}", "", "- 状态: ERROR（结构化结果缺失）", ""])
                continue
            finding = finding_by_result.get(result.id)
            lines.extend(
                [
                    f"### {check.title}",
                    "",
                    f"- Check ID: `{check.id}`",
                    f"- 状态 / 严重度: **{result.status.value.upper()}** / `{result.severity.value}`",
                    f"- 探针: `{check.probe.tool}`",
                    f"- 阈值: `{_json([item.model_dump(mode='json') for item in check.assertions])}`",
                    f"- 观测: `{_json(result.observed)}`",
                    f"- 解释: {_text(result.explanation or result.error_message or '-')}",
                    f"- Evidence Ref: `{_json(result.evidence_refs)}`",
                    f"- Runbook: `{_text(check.runbook_ref or '-')}`",
                    f"- Finding: `{finding.id if finding else '-'}`",
                    "",
                ]
            )

        lines.extend(["## 与上次运行的变化", ""])
        if previous_run is None or previous_run.pack_version != run.pack_version:
            lines.append("- 无兼容的上一次运行可比较。")
        else:
            delta = {
                "pass": run.pass_count - previous_run.pass_count,
                "warn": run.warn_count - previous_run.warn_count,
                "fail": run.fail_count - previous_run.fail_count,
                "error": run.error_count - previous_run.error_count,
            }
            lines.append(f"- 计数变化: `{_json(delta)}`")

        lines.extend(
            [
                "",
                "## 证据与审计",
                "",
                f"- Collector capability hash: `{run.collector_capability_hash}`",
                f"- Session: `{run.session_id or '-'}`",
                "- 完整审计链和文件哈希位于认证下载的 Evidence Package。",
                "",
                "## 限制与缺失数据",
                "",
            ]
        )
        missing = [item for item in results if item.status.value in {"error", "skipped"}]
        if missing:
            for item in missing:
                lines.append(f"- `{item.check_id}`: `{item.error_code or item.status.value}` {_text(item.error_message or '')}".rstrip())
        else:
            lines.append("- 无已知缺失数据。")
        return "\n".join(lines).rstrip() + "\n"
