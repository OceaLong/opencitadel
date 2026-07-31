#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof for KB active-build partial uniqueness."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.errors.exceptions import ConflictError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.user import User
from app.domain.services.knowledge_base.version_builder import (
    KnowledgeBuildCommand,
    KnowledgeVersionBuilder,
    _retry_command_key,
)
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.resource_governance import ResourceBuildORM
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


RUN_POSTGRES_INTEGRATION = (
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") == "1"
)


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL "
        "KB retry active-pin lock proof"
    ),
)
@pytest.mark.asyncio
async def test_retry_kb_row_lock_blocks_publish_and_prevents_stale_parent(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    user_id = f"kb-retry-pin-user-{suffix}"
    kb_id = f"kb-retry-pin-{suffix}"
    base_version_id = f"base-{suffix}"
    failed_version_id = f"failed-version-{suffix}"
    publish_version_id = f"publish-version-{suffix}"
    failed_build = ResourceBuild(
        id=f"failed-build-{suffix}",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id=kb_id,
        version_id=failed_version_id,
        parent_version_id=base_version_id,
        command_key="failed-command",
        state=BuildState.FAILED,
        created_by=user_id,
    )
    publish_build = ResourceBuild(
        id=f"publish-build-{suffix}",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id=kb_id,
        version_id=publish_version_id,
        parent_version_id=base_version_id,
        command_key="publishing-command",
        state=BuildState.RUNNING,
        created_by=user_id,
    )
    system = AuthorizationContext.system("knowledge-retry-pin-race-test")
    scope = OwnerScope.personal(user_id)
    lock_acquired = asyncio.Event()
    release_retry = asyncio.Event()

    class CoordinatedKnowledgeBaseRepository(
        DBKnowledgeBaseRepository
    ):
        async def get_kb_for_update(self, requested_id, scope=None):
            resource = await super().get_kb_for_update(
                requested_id,
                scope=scope,
            )
            lock_acquired.set()
            await release_retry.wait()
            return resource

    class CoordinatedRetryUow(DBUnitOfWork):
        async def __aenter__(self):
            entered = await super().__aenter__()
            self.knowledge_base = CoordinatedKnowledgeBaseRepository(
                self.db_session
            )
            return entered

    builder = KnowledgeVersionBuilder(
        lambda: CoordinatedRetryUow(
            session_factory,
            authorization_context=system,
        )
    )
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            setup.add(
                UserORM.from_domain(
                    User(
                        id=user_id,
                        email=f"{suffix}@example.invalid",
                        username=f"kb-retry-pin-{suffix}",
                    )
                )
            )
            kb_record = KnowledgeBaseModel.from_domain(
                KnowledgeBase(
                    id=kb_id,
                    name="retry pin race",
                    owner_user_id=user_id,
                )
            )
            setup.add(kb_record)
            await setup.flush()
            builds = DBResourceGovernanceRepository(setup)
            versions = DBKnowledgeVersionRepository(setup)
            await builds.add_build(failed_build)
            await builds.add_build(publish_build)
            await versions.create_candidate(
                KnowledgeBaseVersion(
                    id=base_version_id,
                    knowledge_base_id=kb_id,
                    state=KnowledgeVersionState.READY,
                    capabilities={"keyword_search": True},
                    published_at=datetime.now(timezone.utc),
                )
            )
            await versions.create_candidate(
                KnowledgeBaseVersion(
                    id=failed_version_id,
                    knowledge_base_id=kb_id,
                    parent_version_id=base_version_id,
                    build_id=failed_build.id,
                    state=KnowledgeVersionState.FAILED,
                )
            )
            await versions.create_candidate(
                KnowledgeBaseVersion(
                    id=publish_version_id,
                    knowledge_base_id=kb_id,
                    parent_version_id=base_version_id,
                    build_id=publish_build.id,
                )
            )
            kb_record.active_version_id = base_version_id
            await setup.commit()

        retry_task = asyncio.create_task(
            builder.retry_candidate(
                kb_id,
                failed_build.id,
                actor_id=user_id,
                scope=scope,
            )
        )
        await asyncio.wait_for(lock_acquired.wait(), timeout=2.0)

        async def publish() -> bool:
            async with session_factory() as publishing:
                await configure_session_authorization(publishing, system)
                published = await DBKnowledgeVersionRepository(
                    publishing
                ).publish_candidate(
                    publish_version_id,
                    knowledge_base_id=kb_id,
                    expected_active_version_id=base_version_id,
                    state=KnowledgeVersionState.READY,
                    capabilities={"keyword_search": True},
                    degraded_reasons=[],
                    metrics={},
                )
                await publishing.commit()
                return published

        publish_task = asyncio.create_task(publish())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(publish_task),
                timeout=0.25,
            )
        assert not publish_task.done()

        release_retry.set()
        with pytest.raises(ConflictError, match="different"):
            await retry_task
        assert await publish_task is True

        async with session_factory() as verify:
            await configure_session_authorization(verify, system)
            kb_record = (
                await verify.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == kb_id
                    )
                )
            ).scalar_one()
            builds = (
                await verify.execute(
                    select(ResourceBuildORM).where(
                        ResourceBuildORM.resource_id == kb_id
                    )
                )
            ).scalars().all()
            assert kb_record.active_version_id == publish_version_id
            assert {build.id for build in builds} == {
                failed_build.id,
                publish_build.id,
            }
    finally:
        release_retry.set()
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.execute(
                delete(ResourceBuildORM).where(
                    ResourceBuildORM.resource_id == kb_id
                )
            )
            await cleanup.execute(
                delete(UserORM).where(UserORM.id == user_id)
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL "
        "KB build race proof"
    ),
)
@pytest.mark.asyncio
async def test_identical_and_different_commands_use_controlled_partial_unique_race(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    user_id = f"kb-build-user-{suffix}"
    kb_ids = [
        f"kb-build-same-{suffix}",
        f"kb-build-different-{suffix}",
        f"kb-build-retry-{suffix}",
    ]
    system = AuthorizationContext.system("knowledge-build-race-test")
    scope = OwnerScope.personal(user_id)
    builder = KnowledgeVersionBuilder(
        lambda: DBUnitOfWork(
            session_factory,
            authorization_context=system,
        )
    )
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            setup.add(
                UserORM.from_domain(
                    User(
                        id=user_id,
                        email=f"{suffix}@example.invalid",
                        username=f"kb-build-{suffix}",
                    )
                )
            )
            setup.add_all(
                [
                    KnowledgeBaseModel.from_domain(
                        KnowledgeBase(
                            id=kb_id,
                            name="race",
                            owner_user_id=user_id,
                        )
                    )
                    for kb_id in kb_ids
                ]
            )
            await setup.commit()

        async def run_controlled_race(
            kb_id: str,
            *,
            different_command: bool,
        ) -> None:
            winner_command = KnowledgeBuildCommand.reindex(
                kb_id,
                actor_id=user_id,
                options={"mode": "winner"},
            )
            loser_command = (
                KnowledgeBuildCommand.reindex(
                    kb_id,
                    actor_id=user_id,
                    options={"mode": "loser"},
                )
                if different_command
                else winner_command
            )
            async with (
                session_factory() as winner_session,
                session_factory() as loser_session,
            ):
                await configure_session_authorization(
                    winner_session,
                    system,
                )
                await configure_session_authorization(
                    loser_session,
                    system,
                )
                winner_repo = DBResourceGovernanceRepository(winner_session)
                loser_repo = DBResourceGovernanceRepository(loser_session)
                winner_versions = DBKnowledgeVersionRepository(
                    winner_session
                )

                # Both transactions deliberately observe no active build before
                # either insert. This is the required race precondition.
                assert (
                    await winner_repo.get_active_build(
                        ResourceKind.KNOWLEDGE_BASE,
                        kb_id,
                    )
                    is None
                )
                assert (
                    await loser_repo.get_active_build(
                        ResourceKind.KNOWLEDGE_BASE,
                        kb_id,
                    )
                    is None
                )

                winner_version_id = f"winner-version-{uuid.uuid4().hex}"
                winner_build = ResourceBuild(
                    id=f"winner-build-{uuid.uuid4().hex}",
                    resource_kind=ResourceKind.KNOWLEDGE_BASE,
                    resource_id=kb_id,
                    version_id=winner_version_id,
                    command_key=winner_command.command_key(
                        owner_identity=f"user:{user_id}",
                        base_version_id=None,
                    ),
                    created_by=user_id,
                )
                await winner_repo.add_build(winner_build)
                await winner_versions.create_candidate(
                    KnowledgeBaseVersion(
                        id=winner_version_id,
                        knowledge_base_id=kb_id,
                        build_id=winner_build.id,
                    )
                )

                loser_build = ResourceBuild(
                    id=f"loser-build-{uuid.uuid4().hex}",
                    resource_kind=ResourceKind.KNOWLEDGE_BASE,
                    resource_id=kb_id,
                    version_id=f"loser-version-{uuid.uuid4().hex}",
                    command_key=loser_command.command_key(
                        owner_identity=f"user:{user_id}",
                        base_version_id=None,
                    ),
                    created_by=user_id,
                )
                loser_insert = asyncio.create_task(
                    loser_repo.add_build(loser_build)
                )
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(loser_insert),
                        timeout=0.25,
                    )
                assert not loser_insert.done()

                # Releasing the winning transaction makes PostgreSQL resolve
                # the pending partial-unique insert as the losing constraint
                # violation.
                await winner_session.commit()
                with pytest.raises(IntegrityError) as caught:
                    await loser_insert
                constraint_name = getattr(
                    getattr(caught.value.orig, "diag", None),
                    "constraint_name",
                    None,
                ) or getattr(
                    caught.value.orig,
                    "constraint_name",
                    None,
                )
                assert constraint_name == "uq_resource_builds_active"
                await loser_session.rollback()

            # Recovery is deliberately a fresh UoW, never the failed loser.
            result = await builder.create_candidate(
                winner_command,
                scope=scope,
            )
            assert result.created is False
            assert result.build.id == winner_build.id
            if different_command:
                with pytest.raises(ConflictError, match="different"):
                    await builder.create_candidate(
                        loser_command,
                        scope=scope,
                    )
            else:
                duplicate = await builder.create_candidate(
                    loser_command,
                    scope=scope,
                )
                assert duplicate.created is False
                assert duplicate.build.id == winner_build.id

        await run_controlled_race(
            kb_ids[0],
            different_command=False,
        )
        await run_controlled_race(
            kb_ids[1],
            different_command=True,
        )

        retry_kb_id = kb_ids[2]
        original = await builder.create_candidate(
            KnowledgeBuildCommand.reindex(
                retry_kb_id,
                actor_id=user_id,
            ),
            scope=scope,
        )
        async with session_factory() as terminal_session:
            await configure_session_authorization(
                terminal_session,
                system,
            )
            terminal_builds = DBResourceGovernanceRepository(
                terminal_session
            )
            terminal_versions = DBKnowledgeVersionRepository(
                terminal_session
            )
            await terminal_builds.append_event(
                original.build.id,
                ResourceBuildEvent(
                    build_id=original.build.id,
                    seq=0,
                    state=BuildState.FAILED,
                    progress=0.0,
                    payload={"error": "controlled retry source"},
                ),
            )
            assert await terminal_versions.fail_candidate(
                original.version.id,
                knowledge_base_id=retry_kb_id,
            )
            await terminal_session.commit()
            failed_original = await terminal_builds.get_build(
                original.build.id
            )
            assert failed_original is not None
            assert failed_original.state is BuildState.FAILED

        retry_key = _retry_command_key(
            failed_original,
            owner_identity=f"user:{user_id}",
            active_version_id=None,
        )
        async with (
            session_factory() as retry_winner_session,
            session_factory() as retry_loser_session,
        ):
            await configure_session_authorization(
                retry_winner_session,
                system,
            )
            await configure_session_authorization(
                retry_loser_session,
                system,
            )
            retry_winner_builds = DBResourceGovernanceRepository(
                retry_winner_session
            )
            retry_loser_builds = DBResourceGovernanceRepository(
                retry_loser_session
            )
            retry_winner_versions = DBKnowledgeVersionRepository(
                retry_winner_session
            )
            assert (
                await retry_winner_builds.get_active_build(
                    ResourceKind.KNOWLEDGE_BASE,
                    retry_kb_id,
                )
                is None
            )
            assert (
                await retry_loser_builds.get_active_build(
                    ResourceKind.KNOWLEDGE_BASE,
                    retry_kb_id,
                )
                is None
            )

            winner_version_id = f"retry-winner-version-{uuid.uuid4().hex}"
            retry_winner = ResourceBuild(
                id=f"retry-winner-build-{uuid.uuid4().hex}",
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=retry_kb_id,
                version_id=winner_version_id,
                parent_version_id=None,
                command_key=retry_key,
                created_by=user_id,
            )
            await retry_winner_builds.add_build(retry_winner)
            await retry_winner_versions.create_candidate(
                KnowledgeBaseVersion(
                    id=winner_version_id,
                    knowledge_base_id=retry_kb_id,
                    parent_version_id=None,
                    build_id=retry_winner.id,
                )
            )

            retry_loser = retry_winner.model_copy(
                update={
                    "id": f"retry-loser-build-{uuid.uuid4().hex}",
                    "version_id": (
                        f"retry-loser-version-{uuid.uuid4().hex}"
                    ),
                }
            )
            retry_loser_insert = asyncio.create_task(
                retry_loser_builds.add_build(retry_loser)
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(retry_loser_insert),
                    timeout=0.25,
                )
            assert not retry_loser_insert.done()
            await retry_winner_session.commit()
            with pytest.raises(IntegrityError) as retry_integrity:
                await retry_loser_insert
            retry_constraint = getattr(
                getattr(retry_integrity.value.orig, "diag", None),
                "constraint_name",
                None,
            ) or getattr(
                retry_integrity.value.orig,
                "constraint_name",
                None,
            )
            assert retry_constraint == "uq_resource_builds_active"
            await retry_loser_session.rollback()

        recovered = await builder.retry_candidate(
            retry_kb_id,
            failed_original.id,
            actor_id=user_id,
            scope=scope,
        )
        assert recovered.created is False
        assert recovered.build.id == retry_winner.id
        duplicate_retry = await builder.retry_candidate(
            retry_kb_id,
            failed_original.id,
            actor_id=user_id,
            scope=scope,
        )
        assert duplicate_retry.created is False
        assert duplicate_retry.build.id == retry_winner.id
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id.in_(kb_ids)
                )
            )
            await cleanup.execute(
                delete(ResourceBuildORM).where(
                    ResourceBuildORM.resource_id.in_(kb_ids)
                )
            )
            await cleanup.execute(
                delete(UserORM).where(UserORM.id == user_id)
            )
            await cleanup.commit()
        await engine.dispose()
