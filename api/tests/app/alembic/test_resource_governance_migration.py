#!/usr/bin/env python
# -*- coding: utf-8 -*-
import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "b6c7d8e9f0a1_add_resource_governance_foundation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("resource_governance_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_sql() -> tuple[object, str]:
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
    finally:
        migration.op = previous_op
    return migration, output.getvalue()


def _downgrade_sql() -> str:
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous_op = migration.op
    migration.op = Operations(context)
    try:
        migration.downgrade()
    finally:
        migration.op = previous_op
    return output.getvalue()


def _table_ddl(sql: str, table: str) -> str:
    start = sql.index(f"CREATE TABLE {table}")
    end = sql.index(");", start) + 2
    return sql[start:end]


def test_migration_follows_tool_policy_metadata_revision():
    migration = _load_migration()

    assert migration.revision == "b6c7d8e9f0a1"
    assert migration.down_revision == "b5c6d7e8f9a0"


def test_upgrade_creates_exact_shared_governance_tables_and_constraints():
    _migration, sql = _upgrade_sql()

    for table in (
        "tool_approval_batches",
        "tool_approval_calls",
        "resource_builds",
        "session_resource_bindings",
        "resource_build_events",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "UNIQUE (tool_call_id)" in sql
    assert "UNIQUE (batch_id, ordinal)" in sql
    assert "UNIQUE (build_id, seq)" in sql
    assert (
        "CREATE INDEX ix_tool_approval_batches_pending "
        "ON tool_approval_batches (session_id, status, created_at)" in sql
    )
    assert (
        "CREATE UNIQUE INDEX uq_resource_builds_active "
        "ON resource_builds (resource_kind, resource_id) "
        "WHERE state IN ('queued', 'running')" in sql
    )
    assert (
        "CREATE UNIQUE INDEX uq_session_resource_bindings_current "
        "ON session_resource_bindings (session_id, resource_kind) "
        "WHERE is_current = true" in sql
    )


def test_shared_resource_ids_remain_polymorphic_without_domain_foreign_keys():
    _migration, sql = _upgrade_sql()

    assert "FOREIGN KEY(resource_id)" not in sql
    assert "FOREIGN KEY(version_id)" not in sql
    assert "knowledge_bases" not in sql
    assert "codebases" not in sql


def test_upgrade_emits_all_required_columns_nullability_and_defaults():
    _migration, sql = _upgrade_sql()
    expected_columns = {
        "tool_approval_batches": {
            "id", "session_id", "status", "expires_at", "created_at", "decided_at",
        },
        "tool_approval_calls": {
            "id", "batch_id", "tool_call_id", "ordinal", "tool_name",
            "normalized_args", "args_hash", "capability", "effect", "idempotency",
            "approval", "concurrency_group", "status", "decided_by", "decided_at",
        },
        "resource_builds": {
            "id", "resource_kind", "resource_id", "version_id", "parent_version_id",
            "command_key", "state", "phase", "progress", "capabilities",
            "degraded_reasons", "metrics", "error_code", "error_message",
            "heartbeat_at", "last_event_seq", "created_by", "created_at",
            "started_at", "finished_at",
        },
        "session_resource_bindings": {
            "id", "session_id", "resource_kind", "resource_id", "version_id",
            "is_current", "supersedes_binding_id", "bound_by", "created_at",
        },
        "resource_build_events": {
            "id", "build_id", "seq", "phase", "state", "progress", "payload",
            "created_at",
        },
    }
    nullable_columns = {
        "tool_approval_batches": {"decided_at"},
        "tool_approval_calls": {"decided_by", "decided_at"},
        "resource_builds": {
            "parent_version_id", "phase", "error_code", "error_message",
            "heartbeat_at", "started_at", "finished_at",
        },
        "session_resource_bindings": {"supersedes_binding_id"},
        "resource_build_events": {"phase"},
    }

    for table, columns in expected_columns.items():
        ddl = _table_ddl(sql, table)
        emitted_columns = {
            line.strip().split(" ", 1)[0]
            for line in ddl.splitlines()[1:]
            if line.startswith("    ") and not line.strip().startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT")
            )
        }
        assert emitted_columns == columns
        for column in columns - nullable_columns[table]:
            line = next(
                line for line in ddl.splitlines()
                if line.strip().startswith(f"{column} ")
            )
            assert "NOT NULL" in line
        for column in nullable_columns[table]:
            line = next(
                line for line in ddl.splitlines()
                if line.strip().startswith(f"{column} ")
            )
            assert "NOT NULL" not in line

    assert "status VARCHAR(32) DEFAULT 'pending' NOT NULL" in sql
    assert "capabilities JSONB DEFAULT '[]'::jsonb NOT NULL" in sql
    assert "degraded_reasons JSONB DEFAULT '[]'::jsonb NOT NULL" in sql
    assert "metrics JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    assert "last_event_seq INTEGER DEFAULT '0' NOT NULL" in sql
    assert "is_current BOOLEAN DEFAULT true NOT NULL" in sql
    assert "payload JSONB DEFAULT '{}'::jsonb NOT NULL" in sql


def test_downgrade_emits_dependency_safe_reverse_path():
    sql = _downgrade_sql()

    drops = [
        "DROP TABLE resource_build_events",
        "DROP TABLE session_resource_bindings",
        "DROP TABLE resource_builds",
        "DROP TABLE tool_approval_calls",
        "DROP TABLE tool_approval_batches",
    ]
    assert all(drop in sql for drop in drops)
    assert [sql.index(drop) for drop in drops] == sorted(
        sql.index(drop) for drop in drops
    )
