import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.application.services.codebase_service import CodebaseService
from app.domain.errors import BadRequestError
from app.domain.models.codebase import CodebaseSourceType
from app.domain.models.file import File
from app.domain.models.inference import EmbeddingModelSettings
from app.domain.models.scope import OwnerScope
from app.interfaces.schemas.codebase import CreateCodebaseRequest


class _CodebaseRepo:
    def __init__(self):
        self.saved = []

    async def save(self, codebase):
        self.saved.append(codebase)


class _FileRepo:
    def __init__(self, files: dict[str, File]):
        self.files = files

    async def get_by_id(self, file_id: str, scope=None):
        file = self.files.get(file_id)
        if file is None:
            return None
        if (
            scope
            and scope.type.value == "personal"
            and (file.owner_user_id != scope.user_id or file.team_id is not None)
        ):
            return None
        if scope and scope.type.value == "team" and file.team_id != scope.team_id:
            return None
        return file

    async def list_by_ids(self, file_ids, scope=None):
        result = []
        for file_id in file_ids:
            file = await self.get_by_id(file_id, scope=scope)
            if file is not None:
                result.append(file)
        return result


class _Uow:
    def __init__(self, codebase_repo: _CodebaseRepo, file_repo: _FileRepo):
        self.codebase = codebase_repo
        self.file = file_repo
        self.execution_commands = MagicMock()
        self.codebase_version = MagicMock()
        self.codebase_version.add_version = AsyncMock(side_effect=lambda version: version)

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Storage:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.download_file = AsyncMock(side_effect=self._download)

    async def _download(self, file_id: str):
        return io.BytesIO(self.payloads[file_id]), File(id=file_id)


def _zip_bytes(entries: dict[str, str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _service(files: dict[str, File], payloads: dict[str, bytes] | None = None):
    codebase_repo = _CodebaseRepo()
    file_repo = _FileRepo(files)
    admission = MagicMock()
    admission.admit = AsyncMock()
    inference_bindings = MagicMock()
    inference_bindings.resolve = AsyncMock(
        return_value=SimpleNamespace(
            id="embedding-1",
            model=SimpleNamespace(settings=EmbeddingModelSettings()),
        )
    )
    service = CodebaseService(
        uow_factory=lambda: _Uow(codebase_repo, file_repo),
        sandbox_factory=MagicMock(),
        file_storage=_Storage(payloads or {}),
        run_admission_service=admission,
        run_control_service=AsyncMock(),
        run_projection=AsyncMock(),
        inference_bindings=inference_bindings,
    )
    service._test_admission = admission
    return service, codebase_repo


@pytest.mark.parametrize(
    "payload",
    [
        {"source_type": "zip", "file_id": None},
        {"source_type": "files", "file_ids": []},
        {"source_type": "git", "git_url": ""},
    ],
)
def test_missing_source_payload_is_rejected_by_schema(payload):
    with pytest.raises(ValidationError):
        CreateCodebaseRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_invalid_git_source_is_rejected_before_persistence():
    service, repo = _service({})

    with pytest.raises(BadRequestError):
        await service.create_codebase(
            "demo",
            CodebaseSourceType.GIT,
            git_url="https://127.0.0.1/repo.git",
            scope=OwnerScope.personal("owner"),
        )

    assert repo.saved == []
    service._test_admission.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unowned_file_source_is_rejected_before_persistence():
    service, repo = _service({"file-1": File(id="file-1", owner_user_id="other")})

    with pytest.raises(BadRequestError):
        await service.create_codebase(
            "demo",
            CodebaseSourceType.FILES,
            file_ids=["file-1"],
            scope=OwnerScope.personal("owner"),
        )

    assert repo.saved == []
    service._test_admission.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_zip_is_downloaded_and_validated_before_persistence():
    service, repo = _service(
        {"zip-1": File(id="zip-1", owner_user_id="owner")},
        {"zip-1": _zip_bytes({"../escape.py": "x"})},
    )

    with pytest.raises(BadRequestError):
        await service.create_codebase(
            "demo",
            CodebaseSourceType.ZIP,
            file_id="zip-1",
            scope=OwnerScope.personal("owner"),
        )

    assert repo.saved == []
    service._test_admission.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_files_source_is_deduplicated_and_serialized():
    service, repo = _service(
        {
            "file-1": File(id="file-1", owner_user_id="owner"),
            "file-2": File(id="file-2", owner_user_id="owner"),
        }
    )

    codebase = await service.create_codebase(
        "demo",
        CodebaseSourceType.FILES,
        file_ids=["file-1", "file-1", "file-2"],
        scope=OwnerScope.personal("owner"),
    )

    assert json.loads(codebase.source_ref) == {"file_ids": ["file-1", "file-2"]}
    assert len(repo.saved) == 1
    service._test_admission.admit.assert_awaited_once()
