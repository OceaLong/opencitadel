"""Infrastructure implementations for reporting, signing, and governance metrics."""

from __future__ import annotations

import hashlib
import hmac
from html import escape

from app.application.ports.observability import GovernanceMetricsPort
from app.application.ports.reporting import EvidenceSignerPort, ReportRendererPort
from app.domain.models.patrol import PatrolCheckResult, PatrolFinding, PatrolRun
from app.infrastructure.external.report.pdf_renderer import (
    PdfUnavailableError,
    render_html_to_pdf,
)
from app.infrastructure.observability.governance_metrics import (
    record_chain_verification,
    record_remediation_transition,
)
from app.infrastructure.observability.patrol_metrics import observe_finalized


class HmacEvidenceSigner(EvidenceSignerPort):
    def __init__(self, *, key_id: str, secret: str) -> None:
        self._key_id = key_id
        self._secret = secret.encode("utf-8")

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


class MarkdownPdfRenderer(ReportRendererPort):
    def render_pdf(self, *, markdown: str, title: str) -> bytes | None:
        html = (
            "<html><head><meta charset='utf-8'><style>"
            "body{font-family:sans-serif;padding:2em;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
            "h1{color:#1e3a5f;}"
            "</style></head><body>"
            f"<h1>{escape(title)}</h1>"
            f"{escape(markdown).replace(chr(10), '<br/>')}"
            "</body></html>"
        )
        try:
            return render_html_to_pdf(html)
        except PdfUnavailableError:
            return None


class PrometheusGovernanceMetricsAdapter(GovernanceMetricsPort):
    def record_chain_verification(self, result: str) -> None:
        record_chain_verification(result)

    def record_remediation_transition(self, status: str) -> None:
        record_remediation_transition(status)

    def observe_patrol_finalized(
        self,
        run: PatrolRun,
        results: list[PatrolCheckResult],
        findings: list[PatrolFinding],
    ) -> None:
        observe_finalized(run, results, findings)
