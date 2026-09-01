"""Canonical PostgreSQL proof for ownership-transfer teardown semantics.

Covers the resource-reassignment SQL that backs the E1 (user delete →
transfer_to_team) and E2 (team delete → transfer_to_owner / cascade) fixes:

* ``DBUserRepository.transfer_personal_resources_to_team`` moves only the
  user's *personal* resources (``team_id IS NULL``) onto a team.
* ``DBTeamRepository.transfer_resources_to_owner`` reassigns team resources to
  a single owner's personal space explicitly (``owner_user_id`` set,
  ``team_id`` NULL) instead of the DB's implicit ``ON DELETE SET NULL``.
* ``DBTeamRepository.delete_resources`` removes team resources outright.

Harness mirrors test_db_resource_binding_postgres.py: a real authenticated
session under a system authorization scope, seeded via ORM inserts.
"""

import uuid

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.team import TeamMemberORM, TeamORM
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_team_repository import DBTeamRepository
from app.infrastructure.repositories.db_user_repository import DBUserRepository
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Seed:
    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.owner_id = f"xfer-owner-{suffix}"
        self.member_id = f"xfer-member-{suffix}"
        self.team_id = f"xfer-team-{suffix}"
        self.dest_team_id = f"xfer-dest-team-{suffix}"
        self.personal_session_id = f"xfer-personal-{suffix}"
        self.team_session_id = f"xfer-team-session-{suffix}"


async def _system_session(session_factory, actor: str):
    session = session_factory()
    await configure_session_authorization(session, AuthorizationContext.system(actor))
    return session


async def _seed(session_factory, seed: _Seed) -> None:
    session = await _system_session(session_factory, "ownership-transfer-setup")
    try:
        for user_id in (seed.owner_id, seed.member_id):
            await session.execute(
                insert(UserORM).values(
                    id=user_id,
                    email=f"{user_id}@test.local",
                    username=user_id,
                )
            )
        for team_id in (seed.team_id, seed.dest_team_id):
            await session.execute(insert(TeamORM).values(id=team_id, name=team_id))
        await session.execute(
            insert(TeamMemberORM).values(team_id=seed.team_id, user_id=seed.owner_id, role="owner")
        )
        # A personal resource of the owner (team_id NULL).
        await session.execute(
            insert(SessionModel).values(
                id=seed.personal_session_id,
                owner_user_id=seed.owner_id,
                status="pending",
                team_id=None,
            )
        )
        # A team resource created by a *different* member.
        await session.execute(
            insert(SessionModel).values(
                id=seed.team_session_id,
                owner_user_id=seed.member_id,
                status="pending",
                team_id=seed.team_id,
            )
        )
        await session.commit()
    finally:
        await session.close()


async def _cleanup(session_factory, seed: _Seed) -> None:
    session = await _system_session(session_factory, "ownership-transfer-cleanup")
    try:
        await session.execute(
            delete(SessionModel).where(
                SessionModel.id.in_([seed.personal_session_id, seed.team_session_id])
            )
        )
        await session.execute(delete(TeamMemberORM).where(TeamMemberORM.team_id == seed.team_id))
        await session.execute(
            delete(TeamORM).where(TeamORM.id.in_([seed.team_id, seed.dest_team_id]))
        )
        await session.execute(
            delete(UserORM).where(UserORM.id.in_([seed.owner_id, seed.member_id]))
        )
        await session.commit()
    finally:
        await session.close()


async def _session_row(session_factory, session_id: str):
    session = await _system_session(session_factory, "ownership-transfer-read")
    try:
        result = await session.execute(
            select(SessionModel.owner_user_id, SessionModel.team_id).where(
                SessionModel.id == session_id
            )
        )
        return result.one_or_none()
    finally:
        await session.close()


@pytest.mark.anyio
@pytest.mark.usefixtures("postgres_integration")
async def test_transfer_personal_resources_to_team_moves_only_personal_rows():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(engine, signing_secret=settings.session_secret)
    seed = _Seed()
    try:
        await _seed(session_factory, seed)

        write = await _system_session(session_factory, "ownership-transfer-user")
        try:
            moved = await DBUserRepository(write).transfer_personal_resources_to_team(
                seed.owner_id, seed.dest_team_id
            )
            await write.commit()
        finally:
            await write.close()

        assert moved == 1
        # Personal resource is now owned by the destination team, owner kept.
        personal = await _session_row(session_factory, seed.personal_session_id)
        assert personal.owner_user_id == seed.owner_id
        assert personal.team_id == seed.dest_team_id
        # The pre-existing team resource is untouched.
        team_row = await _session_row(session_factory, seed.team_session_id)
        assert team_row.team_id == seed.team_id
    finally:
        await _cleanup(session_factory, seed)
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.usefixtures("postgres_integration")
async def test_transfer_resources_to_owner_consolidates_into_personal_space():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(engine, signing_secret=settings.session_secret)
    seed = _Seed()
    try:
        await _seed(session_factory, seed)

        write = await _system_session(session_factory, "ownership-transfer-team")
        try:
            moved = await DBTeamRepository(write).transfer_resources_to_owner(
                seed.team_id, seed.owner_id
            )
            await write.commit()
        finally:
            await write.close()

        assert moved == 1
        # The team resource that a *member* created is now the OWNER's personal
        # resource explicitly (owner rewritten, team_id NULL) — not silently
        # scattered back to its member creator via ON DELETE SET NULL.
        team_row = await _session_row(session_factory, seed.team_session_id)
        assert team_row.owner_user_id == seed.owner_id
        assert team_row.team_id is None
    finally:
        await _cleanup(session_factory, seed)
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.usefixtures("postgres_integration")
async def test_delete_resources_removes_team_rows():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(engine, signing_secret=settings.session_secret)
    seed = _Seed()
    try:
        await _seed(session_factory, seed)

        write = await _system_session(session_factory, "ownership-transfer-cascade")
        try:
            removed = await DBTeamRepository(write).delete_resources(seed.team_id)
            await write.commit()
        finally:
            await write.close()

        assert removed == 1
        assert await _session_row(session_factory, seed.team_session_id) is None
        # Personal resource outside the team is left alone.
        assert await _session_row(session_factory, seed.personal_session_id) is not None
    finally:
        await _cleanup(session_factory, seed)
        await engine.dispose()
