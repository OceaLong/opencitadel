from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.models.user import User
from app.infrastructure.repositories.db_user_repository import DBUserRepository


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_save_flushes_new_user_before_dependent_records_are_written():
    session = Mock()
    session.get = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    repository = DBUserRepository(session)
    user = User(email="invitee@example.test", username="invitee", password_hash="hash")

    await repository.save(user)

    session.add.assert_called_once()
    session.flush.assert_awaited_once_with()
