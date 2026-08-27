from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.codebase_service import CodebaseService
from app.domain.errors import BadRequestError
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.codebase.snapshot_service import CodeSnapshotService


class _ObjectStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.get_keys: list[str] = []

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get_bytes(self, key: str) -> bytes:
        self.get_keys.append(key)
        return self.objects[key]


class _CodebaseRepo:
    def __init__(self, codebase: Codebase) -> None:
        self._codebase = codebase
        self.saved: list[Codebase] = []

    async def get_by_id(self, codebase_id: str, scope=None):
        if codebase_id == self._codebase.id:
            return self._codebase
        return None

    async def save(self, codebase: Codebase) -> None:
        self.saved.append(codebase)


class _CodebaseVersionRepo:
    def __init__(self, version: CodebaseVersion) -> None:
        self._version = version
        self.calls: list[tuple[str, str | None]] = []

    async def get_version(
        self,
        version_id: str,
        *,
        codebase_id: str | None = None,
    ):
        self.calls.append((version_id, codebase_id))
        if version_id == self._version.id and codebase_id == self._version.codebase_id:
            return self._version
        return None


class _Uow:
    def __init__(self, codebase: Codebase, version: CodebaseVersion) -> None:
        self.codebase = _CodebaseRepo(codebase)
        self.codebase_version = _CodebaseVersionRepo(version)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _SessionSandbox:
    def __init__(self, *, sentinel_exists: bool = False) -> None:
        self.sentinel_exists = sentinel_exists
        self.checked_paths: list[str] = []
        self.restores: list[tuple[str, bytes]] = []
        self.writes: list[tuple[str, str]] = []
        self.ensure_calls = 0

    async def check_file_exists(self, filepath: str) -> ToolResult:
        self.checked_paths.append(filepath)
        return ToolResult(success=True, data=self.sentinel_exists)

    async def restore_workspace_snapshot(self, snapshot_id: str, snapshot_data) -> None:
        self.restores.append((snapshot_id, snapshot_data.read()))

    async def ensure_sandbox(self) -> None:
        self.ensure_calls += 1

    async def write_file(
        self,
        filepath: str,
        content: str,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        sudo: bool = False,
    ) -> ToolResult:
        self.writes.append((filepath, content))
        return ToolResult(success=True)


async def _service_fixture():
    materialized = await CodeSnapshotService().create(
        "cbv1",
        {
            "src/main.py": "line one\nline two\nline three\n",
            "README.md": "# Demo\n",
        },
    )
    storage = _ObjectStorage({materialized.snapshot_key: materialized.snapshot_bytes})
    codebase = Codebase(
        id="cb1",
        name="Demo",
        active_version_id="cbv1",
        sandbox_id=None,
        snapshot_key=None,
    )
    version = CodebaseVersion(
        id="cbv1",
        codebase_id="cb1",
        state=CodebaseVersionState.READY,
        published_at=datetime.now(UTC),
        source_snapshot_key=materialized.snapshot_key,
        source_digest=materialized.source_digest,
        source_revision=materialized.source_revision,
    )
    uow = _Uow(codebase, version)
    service = CodebaseService(
        uow_factory=lambda: uow,
        sandbox_factory=MagicMock(),
        file_storage=MagicMock(),
        run_admission_service=AsyncMock(),
        run_control_service=AsyncMock(),
        run_projection=AsyncMock(),
    )
    return service, storage, codebase, version, materialized, uow


@pytest.mark.anyio
async def test_read_source_uses_bound_published_snapshot_without_ingestion_sandbox():
    service, storage, _codebase, version, _materialized, uow = await _service_fixture()

    content = await service.read_source(
        "cb1",
        "src/main.py",
        start_line=2,
        end_line=2,
        codebase_version_id="cbv1",
        object_storage=storage,
    )

    assert content == "line two\n"
    assert storage.get_keys == [version.source_snapshot_key]
    assert uow.codebase_version.calls == [("cbv1", "cb1")]


@pytest.mark.anyio
async def test_read_source_rejects_path_traversal_before_snapshot_lookup():
    service, storage, _codebase, _version, _materialized, _uow = await _service_fixture()

    with pytest.raises(BadRequestError, match=r"(?:目录穿越|相对路径)"):
        await service.read_source(
            "cb1",
            "../secret.py",
            codebase_version_id="cbv1",
            object_storage=storage,
        )

    assert storage.get_keys == []


@pytest.mark.anyio
async def test_attach_to_session_sandbox_restores_bound_snapshot_and_versioned_sentinel():
    service, storage, _codebase, version, materialized, _uow = await _service_fixture()
    sandbox = _SessionSandbox()

    await service.attach_to_session_sandbox(
        "cb1",
        sandbox,
        storage,
        codebase_version_id="cbv1",
    )

    sentinel_path = f"/home/ubuntu/.oc_codebase_attached_cb1_cbv1_{version.source_digest}"
    assert sandbox.checked_paths == [sentinel_path]
    assert sandbox.restores == [("codebase-cb1-cbv1", materialized.snapshot_bytes)]
    assert sandbox.ensure_calls == 1
    assert sandbox.writes == [(sentinel_path, f"attached:cb1:cbv1:{version.source_digest}\n")]


@pytest.mark.anyio
async def test_attach_to_session_sandbox_skips_when_same_version_digest_sentinel_exists():
    service, storage, _codebase, version, _materialized, _uow = await _service_fixture()
    sandbox = _SessionSandbox(sentinel_exists=True)

    await service.attach_to_session_sandbox(
        "cb1",
        sandbox,
        storage,
        codebase_version_id=version.id,
    )

    assert sandbox.checked_paths == [
        f"/home/ubuntu/.oc_codebase_attached_cb1_cbv1_{version.source_digest}"
    ]
    assert sandbox.restores == []
    assert sandbox.writes == []
    assert storage.get_keys == []
