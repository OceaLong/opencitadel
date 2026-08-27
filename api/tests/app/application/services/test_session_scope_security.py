from unittest.mock import AsyncMock

import pytest

from app.application.services.session_service import SessionService
from app.domain.errors import NotFoundError
from app.domain.models.scope import OwnerScope


class _EmptyCodebaseRepo:
    async def get_by_id(self, codebase_id: str, scope=None):
        return None


class _EmptyKnowledgeBaseRepo:
    async def get_kb(self, kb_id: str, scope=None):
        return None


class _SessionUow:
    def __init__(self):
        self.codebase = _EmptyCodebaseRepo()
        self.knowledge_base = _EmptyKnowledgeBaseRepo()
        self.inference_model = AsyncMock()
        self.skill = AsyncMock()
        self.session = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_session_rejects_codebase_outside_owner_scope():
    uow = _SessionUow()
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=AsyncMock(),
        run_projection=AsyncMock(),
        session_list_publisher=AsyncMock(),
    )

    with pytest.raises(NotFoundError, match="代码库"):
        await service.create_session(
            codebase_id="victim-codebase",
            scope=OwnerScope.personal("attacker-user"),
        )

    uow.session.save.assert_not_awaited()


@pytest.mark.anyio
async def test_create_session_rejects_knowledge_base_outside_owner_scope():
    uow = _SessionUow()
    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_factory=AsyncMock(),
        run_projection=AsyncMock(),
        session_list_publisher=AsyncMock(),
    )

    with pytest.raises(NotFoundError, match="知识库"):
        await service.create_session(
            knowledge_base_id="victim-kb",
            scope=OwnerScope.personal("attacker-user"),
        )

    uow.session.save.assert_not_awaited()
