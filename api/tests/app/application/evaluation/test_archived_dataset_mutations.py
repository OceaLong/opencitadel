"""Real service control flow; atomic DB barriers have separate owned-DB tests."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.evaluation.dataset_service import DatasetService
from app.domain.evaluation.dataset import CaseRevision
from app.domain.models.scope import OwnerScope, Principal


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update_case", "from_run", "import_validate"])
async def test_archived_case_mutation_stops_before_object_storage(operation):
    archive = SimpleNamespace(
        require_active=AsyncMock(side_effect=ValueError("evaluation_resource_archived"))
    )
    repo = SimpleNamespace(get_draft=AsyncMock(return_value={"revision": 1, "members": []}))

    @asynccontextmanager
    async def factory(*args):
        yield SimpleNamespace(evaluation_dataset=repo, evaluation_archive=archive)

    service = object.__new__(DatasetService)
    service.uow_factory = factory
    service._begin = AsyncMock(return_value=("digest", None))
    service._put = AsyncMock()

    async def mutate():
        if operation == "import_validate":
            import io

            await service.import_validate(
                OwnerScope.personal("owned"),
                Principal(user_id="owned"),
                dataset_id=uuid4(),
                request_id="new",
                expected_revision=1,
                stream=io.BytesIO(
                    b'{"schema_version":1,"cases":[{"case_key":"a","input":"changed"}]}'
                ),
                content_type="application/json",
            )
        else:
            await service.update_case(
                OwnerScope.personal("owned"),
                Principal(user_id="owned"),
                dataset_id=uuid4(),
                request_id="new",
                expected_revision=1,
                case=CaseRevision(case_key="a", input="changed"),
                operation=operation,
            )

    with pytest.raises(ValueError, match="archived"):
        await mutate()

    archive.require_active.assert_awaited_once()
    service._put.assert_not_awaited()
