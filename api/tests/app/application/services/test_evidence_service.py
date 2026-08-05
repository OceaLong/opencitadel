#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EvidenceService.build_session_evidence_package: signed ZIP evidence bundle.

Fake-collaborator style mirrors tests/app/application/services/
test_governance_profile_service.py (`gov_env`) and
tests/app/application/services/test_patrol_evidence_service.py (SimpleNamespace
+ AsyncMock for the audit/artifact collaborators) — no real DB.
"""
import hashlib
import hmac
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.services import evidence_service as evidence_service_module
from app.application.services.evidence_service import EvidenceService
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.report.pdf_renderer import PdfUnavailableError
from core.config import get_settings


class _FakeSessionRepo:
    def __init__(self, session: Session):
        self._session = session

    async def get_by_id(self, session_id, scope=None):
        return self._session if session_id == self._session.id else None

    async def list_events(self, session_id, limit=5000):
        return []


class _FakeCheckpointRepo:
    async def list_by_session(self, session_id):
        return []


class _FakeAuditRepo:
    async def list_chained(self, *, resource_id=None, limit=None):
        return []


class _FakeUow:
    def __init__(self, session: Session):
        self.session = _FakeSessionRepo(session)
        self.checkpoint = _FakeCheckpointRepo()
        self.audit = _FakeAuditRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeGovernanceProfileService:
    """Records the (session_id, scope) it was called with."""

    def __init__(self, profile: dict):
        self._profile = profile
        self.calls: list[tuple[str, OwnerScope | None]] = []

    async def build_profile(self, session_id, scope=None):
        self.calls.append((session_id, scope))
        return self._profile


def _governance_profile(session_id: str) -> dict:
    return {
        "session": {
            "id": session_id,
            "title": "demo session",
            "status": "completed",
            "gate_profile": "strict",
            "operator_scope": "owned",
            "created_at": "2026-08-01T00:00:00",
            "updated_at": "2026-08-01T01:00:00",
        },
        "chain": {"verified": True, "checked_entries": 2},
        "approvals": [
            {
                "action": "agent_tool_approve",
                "decision": "approve",
                "actor_user_id": "user-1",
                "created_at": "2026-08-01T00:10:00",
                "pending_phase": "TOOL_APPROVAL_PHASE",
                "tool": "browser_click",
                "approval_batch_id": "b1",
                "feedback": None,
            }
        ],
        "gate_hits": [
            {
                "tool": "shell_exec",
                "gated": True,
                "gate_profile": "strict",
                "created_at": "2026-08-01T00:20:00",
            }
        ],
        "checkpoints": [
            {
                "id": "cp-1",
                "anchor_type": "step",
                "label": "before-shell",
                "created_at": "2026-08-01T00:15:00",
            }
        ],
        "terminal": {"status": "completed", "reached_at": "2026-08-01T01:00:00"},
    }


class _EvidenceEnv:
    def __init__(self):
        self.session = Session(
            id="session-1",
            title="demo session",
            owner_user_id="user-1",
            team_id=None,
            operator_scope="owned",
            gate_profile="strict",
            status=SessionStatus.COMPLETED,
        )
        self.uow = _FakeUow(self.session)
        self.audit_service = SimpleNamespace(
            verify_session_chain=AsyncMock(
                return_value={"ok": True, "session_ok": True, "total": 2, "session_entries": 2}
            ),
            build_session_audit_report_json=AsyncMock(
                return_value={"tool_invocations": [], "governance_actions": []}
            ),
            build_session_audit_report=AsyncMock(return_value="# audit report\n"),
        )
        self.artifact_service = SimpleNamespace(
            list_by_session=AsyncMock(return_value=[]),
        )
        self.governance_profile_service = _FakeGovernanceProfileService(
            _governance_profile(self.session.id)
        )
        self.service = EvidenceService(
            lambda: self.uow,
            self.audit_service,
            self.artifact_service,
            self.governance_profile_service,
        )
        self.scope = OwnerScope.personal("user-1")

    async def create_session_with_governance_data(self) -> str:
        return self.session.id

    @staticmethod
    def zip_names(package: bytes) -> set[str]:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            return set(archive.namelist())

    @staticmethod
    def read_manifest(package: bytes) -> dict:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            return json.loads(archive.read("manifest.json"))

    @staticmethod
    def verify_signature(package: bytes) -> bool:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest_bytes = archive.read("manifest.json")
            sig_text = archive.read("chain-signature.txt").decode("utf-8")
            expected = hmac.new(
                get_settings().audit_signing_key.encode(), manifest_bytes, hashlib.sha256
            ).hexdigest()
            if expected not in sig_text:
                return False
            manifest = json.loads(manifest_bytes)
            for name, digest in manifest["file_hashes"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    return False
            return True


@pytest.fixture
def evidence_env(monkeypatch):
    # PDF rendering (weasyprint) needs native system libs (cairo/pango) that
    # this sandbox doesn't have; build_session_evidence_package already
    # degrades gracefully on PdfUnavailableError (pdf_skipped=True), so pin
    # that branch instead of depending on the local machine's native libs —
    # PDF rendering itself is outside this task's scope.
    def _raise(_html: str) -> bytes:
        raise PdfUnavailableError("weasyprint native libs unavailable in test env")

    monkeypatch.setattr(evidence_service_module, "render_html_to_pdf", _raise)
    return _EvidenceEnv()


@pytest.mark.asyncio
async def test_evidence_package_contains_signed_governance_profile(evidence_env):
    sid = await evidence_env.create_session_with_governance_data()
    package = await evidence_env.service.build_session_evidence_package(sid, scope=evidence_env.scope)
    names = evidence_env.zip_names(package)
    assert "governance-profile.json" in names
    assert "governance-profile.md" in names
    manifest = evidence_env.read_manifest(package)
    assert "governance-profile.json" in manifest["file_hashes"]
    assert "governance-profile.md" in manifest["file_hashes"]
    # 签名对 manifest 整体成立，追加文件后验签仍通过
    assert evidence_env.verify_signature(package)


@pytest.mark.asyncio
async def test_evidence_package_passes_scope_through_to_governance_profile(evidence_env):
    sid = await evidence_env.create_session_with_governance_data()
    await evidence_env.service.build_session_evidence_package(sid, scope=evidence_env.scope)
    assert evidence_env.governance_profile_service.calls == [(sid, evidence_env.scope)]


@pytest.mark.asyncio
async def test_governance_profile_md_renders_deterministic_sections(evidence_env):
    sid = await evidence_env.create_session_with_governance_data()
    package = await evidence_env.service.build_session_evidence_package(sid, scope=evidence_env.scope)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        md = archive.read("governance-profile.md").decode("utf-8")
    assert "shell_exec" in md
    assert "agent_tool_approve" in md
    assert "before-shell" in md
    assert "completed" in md


@pytest.mark.asyncio
async def test_governance_profile_export_scrubs_free_text_secrets(evidence_env):
    """Key-based redaction (redact_value) only inspects the field *name*, so
    a secret pasted into a freeform field like approval feedback sails
    through untouched. Both the .md and the .json export must additionally
    regex-scan free text (same defense used by PatrolReportService) so the
    raw credential never lands in the signed evidence package."""
    sid = await evidence_env.create_session_with_governance_data()
    bearer_jwt = (
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    secret_feedback = f"retry looks fine, reused {bearer_jwt} and password=hunter2"
    evidence_env.governance_profile_service._profile["approvals"][0]["feedback"] = secret_feedback

    package = await evidence_env.service.build_session_evidence_package(sid, scope=evidence_env.scope)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        md = archive.read("governance-profile.md").decode("utf-8")
        raw_json = archive.read("governance-profile.json").decode("utf-8")

    for leaked in ("hunter2", "eyJhbGciOiJIUzI1NiJ9", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"):
        assert leaked not in md, f"{leaked!r} leaked into governance-profile.md"
        assert leaked not in raw_json, f"{leaked!r} leaked into governance-profile.json"
    assert "***REDACTED***" in md
    assert "***REDACTED***" in raw_json
