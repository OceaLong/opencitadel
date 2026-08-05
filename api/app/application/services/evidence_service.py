#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Session evidence package builder (ZIP + PDF summary)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.application.services.artifact_service import ArtifactService
from app.application.services.audit_service import AuditService
from app.application.services.governance_profile_service import GovernanceProfileService
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.audit_chain import canonical as canonical_json
from app.domain.utils.audit_redaction import redact_value, scrub_secret_patterns
from app.infrastructure.external.report.pdf_renderer import PdfUnavailableError, render_html_to_pdf
from app.infrastructure.models.session import SessionModel
from core.config import get_settings
from sqlalchemy import func, or_, select


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


def render_governance_profile_md(profile: Dict[str, Any]) -> bytes:
    """Deterministic, LLM-free Markdown rendering of a governance profile.

    Pure function: same input dict always yields the same bytes. All
    string fields are redacted via ``redact_value`` before interpolation,
    matching the defense-in-depth pattern used by PatrolReportService.
    """
    session = profile.get("session") or {}
    chain = profile.get("chain") or {}
    terminal = profile.get("terminal") or {}
    approvals = profile.get("approvals") or []
    gate_hits = profile.get("gate_hits") or []
    checkpoints = profile.get("checkpoints") or []

    lines: list[str] = [
        f"# 治理档案: {_md_value('id', session.get('id'))}",
        "",
        "## Session 概要",
        "",
        f"- 标题: {_md_value('title', session.get('title'))}",
        f"- 状态: {_md_value('status', session.get('status'))}",
        f"- Gate Profile: {_md_value('gate_profile', session.get('gate_profile'))}",
        f"- Operator Scope: {_md_value('operator_scope', session.get('operator_scope'))}",
        f"- 创建时间: {_md_value('created_at', session.get('created_at'))}",
        f"- 更新时间: {_md_value('updated_at', session.get('updated_at'))}",
        f"- 审计链校验: {'通过' if chain.get('verified') else '异常'} (`{chain.get('checked_entries')}` 条)",
        "",
        "## 审批时间线",
        "",
        "| 时间 | 动作 | 决定 | 操作者 | 工具 | 阶段 | 批次 | 反馈 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if approvals:
        for row in approvals:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_value("created_at", row.get("created_at")),
                        _md_value("action", row.get("action")),
                        _md_value("decision", row.get("decision")),
                        _md_value("actor_user_id", row.get("actor_user_id")),
                        _md_value("tool", row.get("tool")),
                        _md_value("pending_phase", row.get("pending_phase")),
                        _md_value("approval_batch_id", row.get("approval_batch_id")),
                        _md_value("feedback", row.get("feedback")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Gate 命中",
            "",
            "| 时间 | 工具 | Gate Profile | Gated |",
            "| --- | --- | --- | --- |",
        ]
    )
    if gate_hits:
        for row in gate_hits:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_value("created_at", row.get("created_at")),
                        _md_value("tool", row.get("tool")),
                        _md_value("gate_profile", row.get("gate_profile")),
                        _md_value("gated", row.get("gated")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
            "",
            "## 检查点",
            "",
            "| 时间 | ID | 类型 | 标签 |",
            "| --- | --- | --- | --- |",
        ]
    )
    if checkpoints:
        for row in checkpoints:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_value("created_at", row.get("created_at")),
                        _md_value("id", row.get("id")),
                        _md_value("anchor_type", row.get("anchor_type")),
                        _md_value("label", row.get("label")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
            "",
            "## 终态",
            "",
            f"- 状态: {_md_value('status', terminal.get('status'))}",
            f"- 到达时间: {_md_value('reached_at', terminal.get('reached_at'))}",
            "",
        ]
    )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


class EvidenceService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
        artifact_service: ArtifactService,
        governance_profile_service: GovernanceProfileService,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._artifact_service = artifact_service
        self._governance_profile_service = governance_profile_service

    async def list_evidence_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        async with self._uow_factory() as uow:
            stmt = (
                select(SessionModel)
                .where(
                    or_(
                        SessionModel.operator_scope.isnot(None),
                        SessionModel.gate_profile.isnot(None),
                    )
                )
                .order_by(SessionModel.updated_at.desc())
                .offset(max(0, offset))
                .limit(max(1, min(limit, 200)))
            )
            result = await uow.db_session.execute(stmt)
            sessions = result.scalars().all()

        items: List[dict[str, Any]] = []
        for record in sessions:
            session_id = record.id
            chain = await self._audit_service.verify_session_chain(session_id)
            logs = await self._audit_service.list_logs(resource_id=session_id, limit=1000)
            tool_count = sum(1 for log in logs if log.action == "agent_tool_invoke")
            gov_count = sum(1 for log in logs if log.action != "agent_tool_invoke")
            items.append(
                {
                    "session_id": session_id,
                    "title": record.title,
                    "operator_scope": record.operator_scope,
                    "gate_profile": record.gate_profile,
                    "status": record.status,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                    "chain_ok": chain.get("session_ok", chain.get("ok", False)),
                    "tool_invocation_count": tool_count,
                    "governance_action_count": gov_count,
                }
            )
        return items

    async def build_session_evidence_package(
        self, session_id: str, scope: Optional[OwnerScope] = None
    ) -> bytes:
        chain = await self._audit_service.verify_session_chain(session_id)
        audit_json = await self._audit_service.build_session_audit_report_json(session_id)
        audit_md = await self._audit_service.build_session_audit_report(session_id)

        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
            if not session:
                raise ValueError(f"会话[{session_id}]不存在")
            checkpoints = await uow.checkpoint.list_by_session(session_id)
            events = await uow.session.list_events(session_id, limit=5000)

        async with self._uow_factory() as uow:
            chained = await uow.audit.list_chained(resource_id=session_id)
        chain_by_id = {log.id: log for log in chained}
        for entry in audit_json.get("tool_invocations", []) + audit_json.get("governance_actions", []):
            log = chain_by_id.get(entry.get("id", ""))
            if log:
                entry["chain_seq"] = log.chain_seq
                entry["prev_hash"] = log.prev_hash
                entry["entry_hash"] = log.entry_hash

        artifacts = await self._artifact_service.list_by_session(session_id)
        artifact_files: dict[str, bytes] = {}
        for art in artifacts:
            try:
                content = await self._artifact_service.get_content(art.id, scope=None, sanitize_html=False)
                ext = "md" if art.kind == "doc" else "html"
                artifact_files[f"reconciliation/{art.id}-{art.title[:40]}.{ext}"] = content
            except Exception:
                continue

        screenshots: dict[str, bytes] = {}
        shot_idx = 0
        for _seq, event in events:
            if getattr(event, "function_name", None) != "browser_screenshot":
                continue
            result = getattr(event, "function_result", None)
            data = getattr(result, "data", None) if result else None
            b64 = data.get("screenshot_base64") if isinstance(data, dict) else None
            if b64:
                shot_idx += 1
                screenshots[f"screenshots/{shot_idx:03d}.png"] = base64.b64decode(b64)

        checkpoints_data = [
            {
                "id": cp.id,
                "anchor_type": cp.anchor_type,
                "created_at": cp.created_at.isoformat() if cp.created_at else None,
            }
            for cp in checkpoints
        ]

        profile = await self._governance_profile_service.build_profile(session_id, scope=scope)
        governance_profile_json = canonical_json(
            _redact_profile_for_export("", profile)
        ).encode("utf-8")
        governance_profile_md = render_governance_profile_md(profile)

        file_hashes: dict[str, str] = {}
        buffer = io.BytesIO()
        pdf_skipped = False
        pdf_bytes: Optional[bytes] = None

        summary_html = (
            f"<html><head><meta charset='utf-8'></head><body>"
            f"<h1>证据摘要</h1><p>Session: {session_id}</p>"
            f"<p>Operator scope: {session.operator_scope}</p>"
            f"<p>Gate profile: {session.gate_profile}</p>"
            f"<p>链校验: {'通过' if chain.get('session_ok') else '异常'}</p>"
            f"<p>工具调用: {len(audit_json.get('tool_invocations', []))}</p>"
            f"<p>治理动作: {len(audit_json.get('governance_actions', []))}</p>"
            f"</body></html>"
        )
        try:
            pdf_bytes = render_html_to_pdf(summary_html)
        except PdfUnavailableError:
            pdf_skipped = True

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            def _add(name: str, data: bytes) -> None:
                zf.writestr(name, data)
                file_hashes[name] = hashlib.sha256(data).hexdigest()

            _add("audit.json", json.dumps(audit_json, ensure_ascii=False, indent=2).encode("utf-8"))
            _add("audit-report.md", audit_md.encode("utf-8"))
            _add("checkpoints.json", json.dumps(checkpoints_data, ensure_ascii=False, indent=2).encode("utf-8"))
            _add("governance-profile.json", governance_profile_json)
            _add("governance-profile.md", governance_profile_md)
            for name, data in screenshots.items():
                _add(name, data)
            for name, data in artifact_files.items():
                _add(name, data)
            if pdf_bytes:
                _add("evidence-summary.pdf", pdf_bytes)

            manifest: Dict[str, Any] = {
                "session_id": session_id,
                "title": session.title,
                "operator_scope": session.operator_scope,
                "operator_domains": session.operator_domains,
                "gate_profile": session.gate_profile,
                "generated_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "chain_verification": chain,
                "file_hashes": file_hashes,
                "pdf": "skipped" if pdf_skipped else "included",
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            _add("manifest.json", manifest_bytes)

            settings = get_settings()
            secret = settings.audit_signing_key
            sig = hmac.new(secret.encode(), manifest_bytes, hashlib.sha256).hexdigest()
            sig_text = (
                f"manifest HMAC-SHA256: {sig}\n"
                "Verify: HMAC-SHA256("
                f"AUDIT_SIGNING_KEY[{settings.audit_signing_key_id}], "
                "manifest.json bytes)\n"
            ).encode("utf-8")
            _add("chain-signature.txt", sig_text)

        return buffer.getvalue()
