"""Privileged helpers reserved for execution-kernel test cleanup and tamper proof."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.execution.family import RunFamily
from app.domain.execution.run import RunState, RunStatus
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    RuntimePolicyHead,
    derive_run_policy_snapshot,
    policy_digest,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri

_TEST_EXECUTION_REVISION_ID = UUID("ed000000-0000-0000-0000-000000000001")
_TEST_OPERATIONS_REVISION_ID = UUID("ed000000-0000-0000-0000-000000000002")
_TEST_RUN_ID = UUID("80000000-0000-0000-0000-000000000001")
_TEST_POLICY_TIME = datetime(2026, 8, 26, tzinfo=UTC)


def run_policy_snapshot_json(
    family: RunFamily | str,
    *,
    policy: ExecutionPolicy | None = None,
) -> dict[str, object]:
    """Build the deterministic greenfield Run snapshot used by execution tests."""
    resolved_family = family if isinstance(family, RunFamily) else RunFamily(family)
    resolved_policy = policy or ExecutionPolicy()
    active = ActiveExecutionPolicy(
        head=RuntimePolicyHead(
            version=1,
            execution_revision_id=_TEST_EXECUTION_REVISION_ID,
            operations_revision_id=_TEST_OPERATIONS_REVISION_ID,
            updated_by="execution-test",
            updated_at=_TEST_POLICY_TIME,
        ),
        revision=ExecutionPolicyRevision(
            id=_TEST_EXECUTION_REVISION_ID,
            sequence=1,
            schema_version=1,
            policy=resolved_policy,
            digest=policy_digest(1, resolved_policy),
            created_by="execution-test",
            note="execution test snapshot",
            created_at=_TEST_POLICY_TIME,
        ),
    )
    return derive_run_policy_snapshot(active, resolved_family).model_dump(mode="json")


def run_execution_context_for(
    family: RunFamily | str,
    *,
    run_id: UUID | None = None,
    owner_user_id: str | None = "user-1",
    team_id: str | None = None,
    policy: ExecutionPolicy | None = None,
):
    """Build a verified owning Run context for Activity and decision tests."""
    from app.application.execution.run_context import run_execution_context

    resolved_family = family if isinstance(family, RunFamily) else RunFamily(family)
    resolved_run_id = run_id or _TEST_RUN_ID
    return run_execution_context(
        RunState(
            run_id=resolved_run_id,
            family=resolved_family,
            source_entity_type="test",
            source_entity_id=str(resolved_run_id),
            semantic_payload={},
            policy_snapshot=run_policy_snapshot_json(
                resolved_family,
                policy=policy,
            ),
            status=RunStatus.RUNNING,
            stream_version=2,
            owner_user_id=owner_user_id,
            team_id=team_id,
            correlation_id=resolved_run_id,
        )
    )


def execution_kernel_database_uri(*, async_driver: bool = True) -> str:
    """Build the dedicated kernel-login URI used by execution integration tests."""
    settings = load_deployment_settings()
    user = (os.environ.get("POSTGRES_KERNEL_USER") or "").strip()
    password = os.environ.get("POSTGRES_KERNEL_PASSWORD") or ""
    if not user or not password:
        raise RuntimeError(
            "POSTGRES_KERNEL_USER and POSTGRES_KERNEL_PASSWORD are required "
            "for execution integration tests"
        )
    driver = "postgresql+asyncpg" if async_driver else "postgresql+psycopg2"
    return (
        make_url(settings.sqlalchemy_database_uri)
        .set(
            drivername=driver,
            username=user,
            password=password,
        )
        .render_as_string(hide_password=False)
    )


def execution_admin_database_uri() -> str:
    uri = sqlalchemy_sync_migration_database_uri(load_deployment_settings())
    if "+psycopg2" in uri:
        return uri.replace("+psycopg2", "+asyncpg")
    if uri.startswith("postgresql://"):
        return uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    return uri


def authenticated_session_factory(
    engine: AsyncEngine,
    *,
    signing_secret: str,
) -> async_sessionmaker[AsyncSession]:
    """Build a test session factory with the production authorization contract."""
    if not signing_secret:
        raise ValueError("test database authorization signing secret must not be empty")
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={
            "database_authorization_signing_secret": signing_secret,
        },
    )


@asynccontextmanager
async def execution_admin_session() -> AsyncIterator[AsyncSession]:
    """Open a short-lived DDL-owner session, never a runtime application session."""
    settings = load_deployment_settings()
    engine = create_async_engine(execution_admin_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    try:
        async with session_factory() as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("execution-test-admin"),
            )
            yield session
    finally:
        await engine.dispose()


__all__ = [
    "authenticated_session_factory",
    "execution_admin_database_uri",
    "execution_admin_session",
    "execution_kernel_database_uri",
    "run_execution_context_for",
    "run_policy_snapshot_json",
]
