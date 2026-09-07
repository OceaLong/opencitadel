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


@pytest.mark.anyio
async def test_profile_save_cannot_restore_a_password_or_revoked_token_version():
    from app.infrastructure.models.user import UserORM

    current = User(
        email="alice@example.com", username="alice", password_hash="new", token_version=4
    )
    record = UserORM.from_domain(current)
    stale = current.model_copy(
        update={"password_hash": "old", "token_version": 2, "display_name": "Alice"}
    )
    session = Mock()
    session.get = AsyncMock(return_value=record)
    session.flush = AsyncMock()
    await DBUserRepository(session).save(stale)
    assert record.password_hash == "new"
    assert record.token_version == 4
    assert record.display_name == "Alice"


@pytest.mark.anyio
async def test_password_replacement_is_conditional_and_increments_version_in_database():
    from sqlalchemy.dialects import postgresql

    session = Mock()
    session.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value="alice")))
    replaced = await DBUserRepository(session).replace_password(
        "alice",
        expected_hash="old-hash",
        expected_version=3,
        password_hash="new-hash",
    )
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert replaced
    assert statement.params["password_hash"] == "new-hash"
    assert statement.params["password_hash_1"] == "old-hash"
    assert statement.params["token_version_2"] == 3
    assert statement.params["token_version_1"] == 1
    assert "users.token_version +" in str(statement)
    assert "users.password_hash =" in str(statement)
    session.execute.return_value.scalar_one_or_none.return_value = None
    assert not await DBUserRepository(session).replace_password(
        "alice",
        expected_hash="old-hash",
        expected_version=3,
        password_hash="new-hash",
    )
