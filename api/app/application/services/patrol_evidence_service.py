"""Owner-scoped Patrol evidence ZIP extension with canonical HMAC manifest."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath

from app.application.ports.reporting import EvidenceSignerPort
from app.application.services.audit_service import AuditService
from app.application.services.evidence_service import EvidenceService
from app.application.services.patrol_report_service import PatrolReportService
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


class PatrolEvidenceService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        evidence_service: EvidenceService,
        audit_service: AuditService,
        evidence_signer: EvidenceSignerPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._evidence_service = evidence_service
        self._audit_service = audit_service
        self._evidence_signer = evidence_signer

    async def build_package(self, run_id: str, scope: OwnerScope, *, actor_user_id: str) -> bytes:
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run(run_id, scope)
            if run is None or not run.session_id:
                from app.domain.errors import NotFoundError

                raise NotFoundError("Patrol Run 不存在", error_key="apiErrors.patrol.runNotFound")
            results = await uow.patrol.list_check_results(run_id, scope)
            findings = await uow.patrol.list_findings(run_id, scope)

        base = await self._evidence_service.build_session_evidence_package(run.session_id)
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(base), "r") as source:
            for info in source.infolist():
                if (
                    not _safe_member(info.filename)
                    or info.is_dir()
                    or info.filename in {"manifest.json", "chain-signature.txt"}
                ):
                    continue
                files[info.filename] = source.read(info)

        report = PatrolReportService.render(run, results, findings)
        evidence_index = [
            {"check_id": item.check_id, "evidence_refs": item.evidence_refs} for item in results
        ]
        files.update(
            {
                "patrol/run.json": _canonical(run.model_dump(mode="json")),
                "patrol/pack-snapshot.json": _canonical(run.pack_snapshot),
                "patrol/check-results.json": _canonical(
                    [item.model_dump(mode="json") for item in results]
                ),
                "patrol/findings.json": _canonical(
                    [item.model_dump(mode="json") for item in findings]
                ),
                "patrol/report.md": report.encode("utf-8"),
                "patrol/evidence-index.json": _canonical(evidence_index),
            }
        )
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
        manifest = {
            "schema_version": 1,
            "run_id": run.id,
            "session_id": run.session_id,
            "generated_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "file_hashes": hashes,
            "signing_key_id": self._evidence_signer.key_id,
        }
        manifest_bytes = _canonical(manifest)
        signature = self._evidence_signer.sign(manifest_bytes).encode("ascii")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("chain-signature.txt", b"manifest HMAC-SHA256: " + signature + b"\n")

        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action="patrol_evidence_downloaded",
                resource_type="patrol_run",
                resource_id=run.id,
                team_id=run.pack_snapshot.get("team_id"),
                session_id=run.session_id,
                metadata={
                    "session_id": run.session_id,
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                },
            )
        )
        return output.getvalue()
