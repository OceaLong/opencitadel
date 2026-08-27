import hashlib
import hmac
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_evidence_service import PatrolEvidenceService
from app.domain.models.patrol import PatrolRun, PatrolRunStatus, PatrolTriggerType
from app.domain.models.scope import OwnerScope
from tests.app.application_test_support import FixedEvidenceSigner


class Repo:
    def __init__(self, run):
        self.run = run

    async def get_run(self, run_id, scope):
        return self.run if run_id == self.run.id and scope.user_id == "user-1" else None

    async def list_check_results(self, run_id, scope):
        return []

    async def list_findings(self, run_id, scope):
        return []


class Uow:
    def __init__(self, repo):
        self.patrol = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _base_zip():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("audit.json", b"{}")
        archive.writestr("../escape.txt", b"unsafe")
        archive.writestr("manifest.json", b"old")
    return data.getvalue()


@pytest.mark.asyncio
async def test_patrol_evidence_package_is_owner_scoped_safe_hashed_and_signed():
    config = load_patrol_template("kubernetes-baseline-v1")
    run = PatrolRun(
        pack_id="pack-1",
        session_id="session-1",
        execution_run_id=uuid4(),
        pack_version=1,
        pack_snapshot={"name": "Daily", "config": config.model_dump(mode="json")},
        trigger_type=PatrolTriggerType.MANUAL,
        status=PatrolRunStatus.COMPLETED,
        idempotency_key="trigger-1",
        collector_capability_hash="c" * 64,
    )
    audit = SimpleNamespace(record=AsyncMock())
    evidence = SimpleNamespace(build_session_evidence_package=AsyncMock(return_value=_base_zip()))
    signer = FixedEvidenceSigner(secret="patrol-evidence-secret")
    service = PatrolEvidenceService(lambda: Uow(Repo(run)), evidence, audit, signer)

    payload = await service.build_package(
        run.id, OwnerScope.personal("user-1"), actor_user_id="user-1"
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "../escape.txt" not in names
        assert {
            "audit.json",
            "patrol/run.json",
            "patrol/pack-snapshot.json",
            "patrol/check-results.json",
            "patrol/findings.json",
            "patrol/report.md",
            "patrol/evidence-index.json",
            "manifest.json",
            "chain-signature.txt",
        }.issubset(names)
        manifest_bytes = archive.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        for name, digest in manifest["file_hashes"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        expected = hmac.new(
            b"patrol-evidence-secret",
            manifest_bytes,
            hashlib.sha256,
        ).hexdigest()
        assert expected.encode() in archive.read("chain-signature.txt")
    audit.record.assert_awaited_once()
