#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Migration contract for pruning an immutable version's direct parent."""
import importlib.util
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM


MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "d8e9f0a1b2c3_make_kb_version_parent_gc_safe.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "knowledge_version_gc_migration",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline_sql(action: str) -> str:
    module = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous_op = module.op
    module.op = Operations(context)
    try:
        getattr(module, action)()
    finally:
        module.op = previous_op
    return " ".join(output.getvalue().split())


def test_d8_is_single_linear_head_after_c7():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(script.get_heads()) == 1, "migration chain must stay linear"
    assert script.get_revision("d8e9f0a1b2c3").down_revision == "c7d8e9f0a1b2"


def test_upgrade_uses_column_specific_set_null_and_downgrade_restores_restrict():
    upgrade = _offline_sql("upgrade")
    downgrade = _offline_sql("downgrade")

    assert (
        "DROP CONSTRAINT fk_knowledge_base_versions_parent_owner" in upgrade
    )
    assert (
        "FOREIGN KEY(parent_version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id) "
        "ON DELETE SET NULL (parent_version_id)" in upgrade
    )
    assert (
        "FOREIGN KEY(parent_version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id) "
        "ON DELETE SET NULL" not in downgrade
    )
    assert (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_session_resource_bindings_resource_version ON "
        "session_resource_bindings (resource_kind, resource_id, version_id)"
        in upgrade
    )
    assert (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_resource_builds_resource_version_state ON "
        "resource_builds (resource_kind, resource_id, version_id, state)"
        in upgrade
    )
    assert (
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "ix_resource_builds_resource_version_state" in downgrade
    )
    assert (
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "ix_session_resource_bindings_resource_version" in downgrade
    )
    assert upgrade.index("ALTER TABLE knowledge_base_versions") < (
        upgrade.index(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_session_resource_bindings_resource_version"
        )
    )
    assert "COMMIT;" in upgrade


def test_upgrade_recovers_an_invalid_concurrent_index_before_recreating_it():
    migration = _load_migration()
    statements: list[str] = []
    autocommit_entries = 0

    class _Result:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _Bind:
        def execute(self, statement, params=None):
            index_name = (params or {}).get("index_name")
            if index_name == "ix_session_resource_bindings_resource_version":
                return _Result((False,))
            return _Result((True,))

    class _Context:
        as_sql = False

        @contextmanager
        def autocommit_block(self):
            nonlocal autocommit_entries
            autocommit_entries += 1
            yield

    class _Op:
        def get_context(self):
            return _Context()

        def get_bind(self):
            return _Bind()

        def execute(self, statement):
            statements.append(" ".join(str(statement).split()))

    previous_op = migration.op
    migration.op = _Op()
    try:
        migration._create_concurrent_indexes()
    finally:
        migration.op = previous_op

    assert statements == [
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "ix_session_resource_bindings_resource_version",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_session_resource_bindings_resource_version ON "
        "session_resource_bindings "
        "(resource_kind, resource_id, version_id)",
    ]
    assert autocommit_entries == 2


def test_orm_postgres_ddl_matches_column_specific_parent_cleanup():
    ddl = " ".join(
        str(
            CreateTable(KnowledgeBaseVersionORM.__table__).compile(
                dialect=postgresql.dialect()
            )
        ).split()
    )

    assert (
        "FOREIGN KEY(parent_version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id) "
        "ON DELETE SET NULL (parent_version_id)" in ddl
    )
