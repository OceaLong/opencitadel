"""Application-facing report rendering and evidence signing capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class ReportRendererPort(Protocol):
    def render_pdf(self, *, markdown: str, title: str) -> bytes | None: ...


@runtime_checkable
class EvidenceSignerPort(Protocol):
    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes) -> str: ...


@dataclass(frozen=True)
class AuditVerificationKeyring:
    keys: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class ComplianceRuntimeValues:
    sandbox_driver: str
    metrics_token_configured: bool
    audit_signing_key_id: str
    signing_key_is_default: bool
