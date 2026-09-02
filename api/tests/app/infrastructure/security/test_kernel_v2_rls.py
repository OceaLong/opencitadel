"""PostgreSQL proofs for the greenfield signed RLS boundary."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.security.tenant_rls import RLS_TABLES

SECRET = "kernel-v2-test-signing-secret"
SEPARATOR = "\x1f"


def _claims(*, user_id: str = "", team_id: str = "", admin: bool = False):
    values = {
        "auth_mode": "user",
        "user_id": user_id,
        "team_id": team_id,
        "is_admin": "true" if admin else "false",
        "request_id": "rls-test",
        "system_actor": "",
        "is_auditor": "false",
    }
    signature = hmac.new(
        SECRET.encode(),
        SEPARATOR.join(values.values()).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {**values, "auth_signature": signature}


async def _authorize(connection, **kwargs) -> None:
    await connection.execute(text("SET LOCAL ROLE opencitadel_execution_api"))
    values = _claims(**kwargs)
    for key, value in values.items():
        await connection.execute(
            text("SELECT set_config(:key, :value, true)"),
            {"key": f"app.{key}", "value": value},
        )


@pytest.fixture
def database_uri() -> str:
    uri = os.getenv("KERNEL_V2_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("KERNEL_V2_TEST_DATABASE_URI is required for RLS proofs")
    return uri


@pytest.mark.asyncio
async def test_every_catalog_table_has_forced_rls_and_four_policies(database_uri) -> None:
    engine = create_async_engine(database_uri)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                               count(p.policyname) AS policy_count
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        LEFT JOIN pg_policies p
                          ON p.schemaname = n.nspname AND p.tablename = c.relname
                        WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
                        GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                        """
                    ),
                    {"tables": list(RLS_TABLES)},
                )
            ).all()
    finally:
        await engine.dispose()
    assert {row.relname for row in rows} == set(RLS_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
    assert all(row.policy_count == 4 for row in rows)


@pytest.mark.asyncio
async def test_signed_personal_and_team_scope_hide_unrelated_runs(database_uri) -> None:
    engine = create_async_engine(database_uri)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM kernel_runs"))
            for run_id, owner, team in (
                (UUID(int=9301), "user-1", None),
                (UUID(int=9302), "user-2", None),
                (UUID(int=9303), None, "team-1"),
                (UUID(int=9304), None, "team-2"),
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO kernel_runs
                            (id, workflow, created_by_user_id, stream_version, stream_hash,
                             snapshot_version, snapshot_hash, snapshot, created_at, updated_at,
                             owner_user_id, team_id)
                        VALUES (:id, 'agent', 'seed', 0, :hash, NULL, NULL, NULL,
                                :now, :now, :owner, :team)
                        """
                    ),
                    {
                        "id": run_id,
                        "hash": "0" * 64,
                        "now": now,
                        "owner": owner,
                        "team": team,
                    },
                )
        async with engine.begin() as connection:
            await _authorize(connection, user_id="user-1")
            personal = (
                (await connection.execute(text("SELECT id FROM kernel_runs ORDER BY id")))
                .scalars()
                .all()
            )
        async with engine.begin() as connection:
            await _authorize(connection, user_id="member-1", team_id="team-1")
            team = (
                (await connection.execute(text("SELECT id FROM kernel_runs ORDER BY id")))
                .scalars()
                .all()
            )
        async with engine.begin() as connection:
            await _authorize(connection, user_id="admin-1", admin=True)
            admin_count = await connection.scalar(text("SELECT count(*) FROM kernel_runs"))
    finally:
        await engine.dispose()
    assert personal == [UUID(int=9301)]
    assert team == [UUID(int=9303)]
    assert admin_count == 4


@pytest.mark.asyncio
async def test_owner_scope_cannot_be_changed_after_insert(database_uri) -> None:
    engine = create_async_engine(database_uri)

    async def change_owner() -> None:
        async with engine.begin() as connection:
            # Privileged authorization passes RLS so the immutable-scope
            # trigger itself is proven rather than only WITH CHECK denial.
            await _authorize(connection, user_id="admin-1", admin=True)
            await connection.execute(
                text("UPDATE kernel_runs SET owner_user_id = 'user-2' WHERE id = :id"),
                {"id": UUID(int=9301)},
            )

    try:
        with pytest.raises(Exception, match="owner scope is immutable"):
            await change_owner()
    finally:
        await engine.dispose()
