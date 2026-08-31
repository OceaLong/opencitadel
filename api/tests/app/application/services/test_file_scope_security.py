from io import BytesIO

import pytest

from app.application.services.file_service import FileService
from app.domain.errors import NotFoundError
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope


class _FakeFileRepo:
    def __init__(self):
        self.scope = None

    async def get_by_id(self, file_id: str, scope=None):
        self.scope = scope
        if scope and scope.user_id == "user-1":
            return File(id=file_id, filename="owned.txt", owner_user_id=scope.user_id)
        return None


class _FakeUow:
    def __init__(self, repo):
        self.file = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeStorage:
    def __init__(self):
        self.deleted: list[str] = []

    async def download_file(self, file_id: str):
        return BytesIO(b"ok"), File(id=file_id, filename="raw.txt")

    async def delete_file(self, file_id: str):
        self.deleted.append(file_id)


@pytest.mark.asyncio
async def test_file_download_validates_owner_scope_before_storage_read():
    repo = _FakeFileRepo()
    service = FileService(lambda: _FakeUow(repo), _FakeStorage())
    scope = OwnerScope.personal("user-1")

    file_data, file_info = await service.download_file("file-1", scope=scope)

    assert file_data.read() == b"ok"
    assert file_info.owner_user_id == "user-1"
    assert repo.scope == scope


@pytest.mark.asyncio
async def test_file_download_requires_explicit_owner_scope():
    repo = _FakeFileRepo()
    service = FileService(lambda: _FakeUow(repo), _FakeStorage())

    with pytest.raises(TypeError):
        await service.download_file("file-1")


@pytest.mark.asyncio
async def test_file_delete_validates_owner_scope_before_storage_delete():
    repo = _FakeFileRepo()
    storage = _FakeStorage()
    service = FileService(lambda: _FakeUow(repo), storage)

    await service.delete_file("file-1", scope=OwnerScope.personal("user-1"))

    assert storage.deleted == ["file-1"]


@pytest.mark.asyncio
async def test_file_delete_rejects_non_owner_before_storage_delete():
    # Regression: a non-owner scope must fail ownership check and never reach
    # storage deletion, so one tenant can't delete another tenant's file.
    repo = _FakeFileRepo()
    storage = _FakeStorage()
    service = FileService(lambda: _FakeUow(repo), storage)

    with pytest.raises(NotFoundError):
        await service.delete_file("file-1", scope=OwnerScope.personal("attacker"))

    assert storage.deleted == []
