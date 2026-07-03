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
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.external.report.pdf_renderer import PdfUnavailableError, render_html_to_pdf
from app.infrastructure.models.session import SessionModel
from core.config import get_settings
from sqlalchemy import func, or_, select


class EvidenceService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService,
        artifact_service: ArtifactService,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._artifact_service = artifact_service

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

    async def build_session_evidence_package(self, session_id: str) -> bytes:
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

            secret = get_settings().api_key_secret
            sig = hmac.new(secret.encode(), manifest_bytes, hashlib.sha256).hexdigest()
            sig_text = (
                f"manifest HMAC-SHA256: {sig}\n"
                f"Verify: HMAC-SHA256(API_KEY_SECRET, manifest.json bytes)\n"
            ).encode("utf-8")
            _add("chain-signature.txt", sig_text)

        return buffer.getvalue()
