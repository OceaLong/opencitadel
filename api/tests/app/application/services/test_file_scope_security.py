#!/usr/bin/env python
# -*- coding: utf-8 -*-
from io import BytesIO

import pytest

from app.application.services.file_service import FileService
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
    async def download_file(self, file_id: str):
        return BytesIO(b"ok"), File(id=file_id, filename="raw.txt")


@pytest.mark.asyncio
async def test_file_download_validates_owner_scope_before_storage_read():
    repo = _FakeFileRepo()
    service = FileService(lambda: _FakeUow(repo), _FakeStorage())
    scope = OwnerScope.personal("user-1")

    file_data, file_info = await service.download_file("file-1", scope=scope)

    assert file_data.read() == b"ok"
    assert file_info.owner_user_id == "user-1"
    assert repo.scope == scope
