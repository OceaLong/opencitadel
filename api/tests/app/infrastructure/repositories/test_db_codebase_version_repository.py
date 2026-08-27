from datetime import UTC, datetime

import pytest

from app.domain.models.codebase_version import CodebaseVersionState
from app.infrastructure.models.codebase import CodebaseModel
from app.infrastructure.models.codebase_version import CodebaseVersionORM
from app.infrastructure.repositories.db_codebase_version_repository import (
    DBCodebaseVersionRepository,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, version, codebase):
        self.results = iter((_Result(version), _Result(codebase)))
        self.version = version
        self.codebase = codebase

    async def execute(self, _statement):
        return next(self.results)

    async def flush(self):
        assert self.version.published_at.tzinfo is not None
        assert self.codebase.updated_at.tzinfo is not None


@pytest.mark.asyncio
async def test_publish_uses_column_compatible_timestamps():
    version = CodebaseVersionORM(
        id="v1",
        codebase_id="cb1",
        parent_version_id=None,
        build_id="b1",
        state="building",
        created_at=datetime.now(UTC),
    )
    codebase = CodebaseModel(
        id="cb1",
        name="demo",
        source_type="files",
        status="pending",
        active_version_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository = DBCodebaseVersionRepository(_Session(version, codebase))

    published = await repository.publish_candidate(
        "v1",
        expected_active_version_id=None,
        state=CodebaseVersionState.READY,
        capabilities={"source_read": True},
        degraded_reasons=[],
        metrics={},
    )

    assert published is True
