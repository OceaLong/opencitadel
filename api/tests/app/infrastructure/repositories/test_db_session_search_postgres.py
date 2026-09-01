"""Canonical PostgreSQL proof for session keyword search (E10a).

Exercises the real ``DBSessionRepository.get_all`` SELECT with its ILIKE
title/latest_message filter against a live Postgres, proving the keyword
filter, owner-scope isolation, pagination bounds, and the untouched default
(no ``search``) behavior all hold with real SQL semantics.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_session_repository import DBSessionRepository
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_session_search_filters_by_keyword_scope_and_pagination() -> None:
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(engine, signing_secret=settings.session_secret)

    owner_id = f"search-user-{uuid.uuid4()}"
    other_id = f"search-other-{uuid.uuid4()}"
    owner_scope = OwnerScope.personal(owner_id)
    base = datetime(2026, 8, 1, tzinfo=UTC)

    # (id_suffix, owner, title, latest_message, minutes_offset)
    seed = [
        ("a", owner_id, "Quarterly deploy plan", "checking prod rollout", 10),
        ("b", owner_id, "Random notes", "deploy failed on staging", 20),
        ("c", owner_id, "Vacation ideas", "nothing relevant here", 30),
        ("d", other_id, "Deploy for other tenant", "deploy deploy deploy", 40),
    ]
    created_ids = [f"search-session-{owner_id}-{s}" for s, *_ in seed]

    try:
        async with session_factory() as setup:
            await configure_session_authorization(
                setup, AuthorizationContext.system("session-search-setup")
            )
            for user in (owner_id, other_id):
                await setup.execute(
                    insert(UserORM).values(id=user, email=f"{user}@test.local", username=user)
                )
            for (_suffix, user, title, latest, offset), sid in zip(seed, created_ids, strict=True):
                await setup.execute(
                    insert(SessionModel).values(
                        id=sid,
                        owner_user_id=user,
                        title=title,
                        latest_message=latest,
                        latest_message_at=base + timedelta(minutes=offset),
                        status="pending",
                    )
                )
            await setup.commit()

        async with session_factory() as read:
            await configure_session_authorization(
                read, AuthorizationContext.system("session-search-read")
            )
            repo = DBSessionRepository(read)

            # Keyword matches title OR latest_message, scoped to the owner only.
            hits = await repo.get_all(scope=owner_scope, search="deploy")
            hit_titles = {s.title for s in hits}
            assert hit_titles == {"Quarterly deploy plan", "Random notes"}
            # Other tenant's "Deploy for other tenant" is excluded by scope.
            assert all(s.owner_user_id == owner_id for s in hits)

            # Blank search leaves list behavior unchanged (all owner sessions).
            unfiltered = await repo.get_all(scope=owner_scope)
            assert {s.title for s in unfiltered} == {
                "Quarterly deploy plan",
                "Random notes",
                "Vacation ideas",
            }
            # Whitespace-only search is treated as no filter.
            blank = await repo.get_all(scope=owner_scope, search="   ")
            assert {s.id for s in blank} == {s.id for s in unfiltered}

            # Pagination still applies on top of the filter (newest first).
            page = await repo.get_all(scope=owner_scope, search="deploy", limit=1, offset=0)
            assert len(page) == 1
            assert page[0].title == "Random notes"  # latest_message_at desc
            page2 = await repo.get_all(scope=owner_scope, search="deploy", limit=1, offset=1)
            assert len(page2) == 1
            assert page2[0].title == "Quarterly deploy plan"

            # LIKE wildcards in user input are treated literally, not as globs.
            assert await repo.get_all(scope=owner_scope, search="%") == []
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(
                cleanup, AuthorizationContext.system("session-search-cleanup")
            )
            await cleanup.execute(delete(SessionModel).where(SessionModel.id.in_(created_ids)))
            await cleanup.execute(delete(UserORM).where(UserORM.id.in_([owner_id, other_id])))
            await cleanup.commit()
        await engine.dispose()
