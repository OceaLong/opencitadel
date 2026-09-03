"""Session evidence package builder (ZIP + PDF summary)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.application.ports.queries import EvidenceSessionQueryPort
from app.application.ports.reporting import EvidenceSignerPort, ReportRendererPort
from app.application.services.artifact_service import ArtifactService
from app.application.services.audit_service import AuditService
from app.application.services.governance_profile_service import GovernanceProfileService
from app.domain.errors import NotFoundError
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.audit_chain import canonical as canonical_json
from app.domain.utils.audit_redaction import redact_value, scrub_secret_patterns

logger = logging.getLogger(__name__)


def _md_value(key: str, value: Any) -> str:
    """Redact then render a single profile field for Markdown, treating
    None/missing uniformly as '-' (mirrors patrol_report_service's
    deterministic-render-only-persisted-fields discipline).

    Two-layer defense, same as PatrolReportService: ``redact_value`` masks
    by field *name* (password/token/... keys) and known PII shapes
    (email/phone); ``scrub_secret_patterns`` additionally regex-scans the
    rendered text itself, so a credential pasted into a freeform field
    (e.g. approval feedback) that isn't caught by the key check still gets
    caught here.
    """
    if value is None:
        return "-"
    safe = redact_value(key, value)
    text = scrub_secret_patterns(safe)
    return text.replace("|", "\\|").replace("\n", " ") or "-"


def _redact_profile_for_export(key: str, value: Any) -> Any:
    """Recursively apply the same two-layer redaction as ``_md_value`` to a
    governance profile before it is serialized to JSON, so
    governance-profile.json gets the same free-text secret scrubbing as
    governance-profile.md rather than only the key-based pass."""
    if isinstance(value, str):
        return scrub_secret_patterns(redact_value(key, value))
    if isinstance(value, dict):
        return {k: _redact_profile_for_export(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_profile_for_export(key, item) for item in value]
    return value


def render_governance_profile_md(profile: dict[str, Any]) -> bytes:
    """Deterministic, LLM-free Markdown rendering of a governance profile.

    Pure function: same input dict always yields the same bytes. All
    string fields are redacted via ``redact_value`` before interpolation,
    matching the defense-in-depth pattern used by PatrolReportService.
    """
    session = profile.get("session") or {}
    chain = profile.get("chain") or {}
    runs = profile.get("runs") or []
    approvals = profile.get("approvals") or []
    activities = profile.get("activities") or []

    lines: list[str] = [
        f"# 治理档案: {_md_value('id', session.get('id'))}",
        "",
        "## Session 概要",
        "",
        f"- 标题: {_md_value('title', session.get('title'))}",
        f"- 所有者: {_md_value('owner_user_id', session.get('owner_user_id'))}",
        f"- 团队: {_md_value('team_id', session.get('team_id'))}",
        f"- 状态: {_md_value('status', session.get('status'))}",
        f"- Operator Scope: {_md_value('operator_scope', session.get('operator_scope'))}",
        f"- Operator Domains: {_md_value('operator_domains', session.get('operator_domains'))}",
        f"- 创建时间: {_md_value('created_at', session.get('created_at'))}",
        f"- 更新时间: {_md_value('updated_at', session.get('updated_at'))}",
        (
            f"- 执行事件链校验: {'通过' if chain.get('verified') else '异常'} "
            f"(`{chain.get('checked_runs')}` Runs / `{chain.get('checked_entries')}` 事件)"
        ),
        "",
        "## Run 时间线",
        "",
        "| Run ID | 类型 | 状态 | 创建时间 | 终止时间 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if runs:
        lines.extend(
            (
                "| "
                + " | ".join(
                    [
                        _md_value("run_id", row.get("run_id")),
                        _md_value("family", row.get("family")),
                        _md_value("status", row.get("status")),
                        _md_value("created_at", row.get("created_at")),
                        _md_value("terminal_at", row.get("terminal_at")),
                    ]
                )
                + " |"
            )
            for row in runs
        )
    else:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## 审批时间线",
            "",
            "| 请求时间 | 决定时间 | 状态 | 操作者 | 对象 | Approval ID | 反馈 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if approvals:
        lines.extend(
            (
                "| "
                + " | ".join(
                    [
                        _md_value("requested_at", row.get("requested_at")),
                        _md_value("decided_at", row.get("decided_at")),
                        _md_value("status", row.get("status")),
                        _md_value("decided_by_user_id", row.get("decided_by_user_id")),
                        _md_value("subject_label", row.get("subject_label")),
                        _md_value("approval_id", row.get("approval_id")),
                        _md_value("feedback", row.get("feedback")),
                    ]
                )
                + " |"
            )
            for row in approvals
        )
    else:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Activity 时间线",
            "",
            "| 创建时间 | Activity ID | 类型 | 状态 | 尝试 | 失败代码 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if activities:
        lines.extend(
            (
                "| "
                + " | ".join(
                    [
                        _md_value("created_at", row.get("created_at")),
                        _md_value("activity_id", row.get("activity_id")),
                        _md_value("activity_type", row.get("activity_type")),
                        _md_value("status", row.get("status")),
                        _md_value("attempt", row.get("attempt")),
                        _md_value("failure_code", row.get("failure_code")),
                    ]
                )
                + " |"
            )
            for row in activities
        )
    else:
        lines.append("| - | - | - | - | - | - |")

    lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


class EvidenceService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
        artifact_service: ArtifactService,
        governance_profile_service: GovernanceProfileService,
        report_renderer: ReportRendererPort,
        evidence_signer: EvidenceSignerPort,
        session_query: EvidenceSessionQueryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._artifact_service = artifact_service
        self._governance_profile_service = governance_profile_service
        self._report_renderer = report_renderer
        self._evidence_signer = evidence_signer
        self._session_query = session_query

    async def list_evidence_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sessions = await self._session_query.list_sessions(limit=limit, offset=offset)

        items: list[dict[str, Any]] = []
        for record in sessions:
            session_id = record.session_id
            if record.team_id:
                scope = OwnerScope.team("evidence-auditor", record.team_id)
            elif record.owner_user_id:
                scope = OwnerScope.personal(record.owner_user_id)
            else:
                continue
            try:
                profile = await self._governance_profile_service.build_profile(
                    session_id,
                    scope=scope,
                )
            except NotFoundError:
                # One inconsistent row (e.g. a session no longer visible in its
                # reconstructed scope) must not 404 the whole compliance
                # listing; such a row has no buildable evidence profile anyway.
                logger.warning("evidence listing skipped session %s: 治理画像不可构建", session_id)
                continue
            items.append(
                {
                    "session_id": session_id,
                    "title": record.title,
                    "owner_user_id": record.owner_user_id,
                    "team_id": record.team_id,
                    "operator_scope": record.operator_scope,
                    "status": record.status,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                    "chain_ok": profile["chain"]["verified"],
                    "tool_invocation_count": sum(
                        1
                        for activity in profile["activities"]
                        if activity["activity_type"] == "tool.call"
                    ),
                    "governance_action_count": len(profile["approvals"]),
                }
            )
        return items

    async def build_session_evidence_package(
        self, session_id: str, scope: OwnerScope | None = None
    ) -> bytes:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id, scope=scope)
            if not session:
                raise ValueError(f"会话[{session_id}]不存在")
        resolved_scope = scope
        if resolved_scope is None:
            if session.team_id:
                resolved_scope = OwnerScope.team("evidence-auditor", session.team_id)
            elif session.owner_user_id:
                resolved_scope = OwnerScope.personal(session.owner_user_id)
            else:
                raise ValueError(f"会话[{session_id}]没有所有权作用域")

        audit_chain = await self._audit_service.verify_session_chain(session_id)
        audit_json = await self._audit_service.build_session_audit_report_json(session_id)
        audit_md = await self._audit_service.build_session_audit_report(session_id)

        async with self._uow_factory() as uow:
            chained = await uow.audit.list_chained(resource_id=session_id)
        chain_by_id = {log.id: log for log in chained}
        for entry in audit_json.get("entries", []):
            log = chain_by_id.get(entry.get("id", ""))
            if log:
                entry["chain_seq"] = log.chain_seq
                entry["prev_hash"] = log.prev_hash
                entry["entry_hash"] = log.entry_hash

        artifacts = await self._artifact_service.list_by_session(
            session_id,
            scope=resolved_scope,
        )
        artifact_files: dict[str, bytes] = {}
        for art in artifacts:
            content = await self._artifact_service.get_content(
                art.id,
                scope=resolved_scope,
                sanitize_html=False,
            )
            ext = "md" if art.kind == "doc" else "html"
            artifact_files[f"artifacts/{art.id}.{ext}"] = content

        profile = await self._governance_profile_service.build_profile(
            session_id,
            scope=resolved_scope,
        )
        governance_profile_json = canonical_json(_redact_profile_for_export("", profile)).encode(
            "utf-8"
        )
        governance_profile_md = render_governance_profile_md(profile)

        file_hashes: dict[str, str] = {}
        buffer = io.BytesIO()
        pdf_skipped = False
        pdf_bytes: bytes | None = None

        summary_markdown = (
            f"Session: {session_id}\n\n"
            f"- Operator scope: {session.operator_scope}\n"
            f"- 执行事件链: {'通过' if profile['chain']['verified'] else '异常'}\n"
            f"- Runs: {len(profile['runs'])}\n"
            f"- Activities: {len(profile['activities'])}\n"
            f"- Approvals: {len(profile['approvals'])}\n"
        )
        pdf_bytes = self._report_renderer.render_pdf(
            markdown=summary_markdown,
            title="证据摘要",
        )
        pdf_skipped = pdf_bytes is None

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            def _add(name: str, data: bytes) -> None:
                zf.writestr(name, data)
                file_hashes[name] = hashlib.sha256(data).hexdigest()

            _add("audit.json", json.dumps(audit_json, ensure_ascii=False, indent=2).encode("utf-8"))
            _add("audit-report.md", audit_md.encode("utf-8"))
            _add("governance-profile.json", governance_profile_json)
            _add("governance-profile.md", governance_profile_md)
            for name, data in artifact_files.items():
                _add(name, data)
            if pdf_bytes:
                _add("evidence-summary.pdf", pdf_bytes)

            manifest: dict[str, Any] = {
                "session_id": session_id,
                "title": session.title,
                "operator_scope": session.operator_scope,
                "operator_domains": session.operator_domains,
                "generated_at": datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "execution_chain_verification": profile["chain"],
                "audit_chain_verification": audit_chain,
                "file_hashes": file_hashes,
                "pdf": "skipped" if pdf_skipped else "included",
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            _add("manifest.json", manifest_bytes)

            sig = self._evidence_signer.sign(manifest_bytes)
            sig_text = (
                f"manifest HMAC-SHA256: {sig}\n"
                "Verify: HMAC-SHA256("
                f"AUDIT_SIGNING_KEY[{self._evidence_signer.key_id}], "
                "manifest.json bytes)\n"
            ).encode()
            _add("chain-signature.txt", sig_text)

        return buffer.getvalue()
