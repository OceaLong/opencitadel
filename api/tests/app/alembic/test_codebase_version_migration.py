#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Contract tests for immutable codebase version storage and legacy v1 backfill."""
import importlib.util
from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.domain.models.codebase import (
    Codebase,
    CodebaseArtifact,
    CodebaseChunk,
    CodebaseEdge,
    CodebaseFile,
    CodebaseStatus,
    CodebaseSymbol,
)
from app.domain.models.codebase_version import (
    CodeEvidenceRef,
    CodebaseVersion,
    CodebaseVersionState,
)
from app.infrastructure.models.base import Base
from app.infrastructure.models.codebase_version import CodebaseVersionORM


MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "e9f0a1b2c3d4_add_codebase_versions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "codebase_version_migration",
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


def test_e9_is_single_linear_head_after_kb_gc_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["e9f0a1b2c3d4"]
    assert script.get_revision("e9f0a1b2c3d4").down_revision == (
        "d8e9f0a1b2c3"
    )


def test_domain_models_have_version_identity_and_evidence_defaults():
    version = CodebaseVersion(
        id="cbv1",
        codebase_id="cb1",
        state=CodebaseVersionState.DEGRADED,
        capabilities={"lexical_search": True, "vector_search": False},
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
    )
    evidence = CodeEvidenceRef(
        version_id=version.id,
        file_id="file1",
        path="src/main.py",
        start_line=1,
        end_line=3,
        analyzer="tree-sitter",
        confidence=0.95,
    )

    assert version.published is False
    assert version.degraded is True
    assert evidence.path == "src/main.py"

    assert Codebase(active_version_id="cbv1").active_version_id == "cbv1"
    assert CodebaseFile(codebase_id="cb1", version_id="cbv1", path="a.py").version_id == "cbv1"
    assert (
        CodebaseSymbol(
            codebase_id="cb1",
            version_id="cbv1",
            file_id="file1",
            name="run",
            qualified_name="pkg.mod.run",
        ).qualified_name
        == "pkg.mod.run"
    )
    assert (
        CodebaseEdge(
            codebase_id="cb1",
            version_id="cbv1",
            src_symbol_id="s1",
            evidence=[evidence],
            resolution="ambiguous",
        ).evidence[0].version_id
        == "cbv1"
    )
    assert CodebaseChunk(codebase_id="cb1", version_id="cbv1").search_text == ""
    assert (
        CodebaseArtifact(
            codebase_id="cb1",
            version_id="cbv1",
            kind="overview",
        ).version_id
        == "cbv1"
    )


def test_orm_metadata_contains_expand_compatible_version_columns():
    assert Base.metadata.tables["codebases"].c.active_version_id.nullable is True
    assert Base.metadata.tables["codebase_files"].c.version_id.nullable is True
    assert Base.metadata.tables["codebase_symbols"].c.version_id.nullable is True
    assert Base.metadata.tables["codebase_edges"].c.version_id.nullable is True
    assert Base.metadata.tables["codebase_chunks"].c.version_id.nullable is True
    assert Base.metadata.tables["codebase_artifacts"].c.version_id.nullable is True
    assert "qualified_name" in Base.metadata.tables["codebase_symbols"].c
    assert "parser" in Base.metadata.tables["codebase_symbols"].c
    assert "confidence" in Base.metadata.tables["codebase_symbols"].c
    assert "resolution" in Base.metadata.tables["codebase_edges"].c
    assert "evidence" in Base.metadata.tables["codebase_edges"].c
    assert "search_text" in Base.metadata.tables["codebase_chunks"].c


def test_orm_postgres_ddl_contains_codebase_version_constraints():
    ddl = " ".join(
        str(
            CreateTable(CodebaseVersionORM.__table__).compile(
                dialect=postgresql.dialect()
            )
        ).split()
    )

    assert "CREATE TABLE codebase_versions" in ddl
    assert "FOREIGN KEY(codebase_id) REFERENCES codebases (id) ON DELETE CASCADE" in ddl
    assert "FOREIGN KEY(build_id) REFERENCES resource_builds (id) ON DELETE SET NULL" in ddl
    assert (
        "FOREIGN KEY(parent_version_id, codebase_id) REFERENCES "
        "codebase_versions (id, codebase_id)"
        in ddl
    )
    assert "UNIQUE (id, codebase_id)" in ddl


def test_upgrade_creates_version_table_columns_and_indexes():
    sql = _offline_sql("upgrade")

    assert "CREATE TABLE codebase_versions" in sql
    for column in (
        "source_snapshot_key TEXT",
        "source_revision TEXT",
        "source_digest VARCHAR(64)",
        "capabilities JSONB DEFAULT '{}'::jsonb NOT NULL",
        "degraded_reasons JSONB DEFAULT '[]'::jsonb NOT NULL",
        "metrics JSONB DEFAULT '{}'::jsonb NOT NULL",
        "legacy_snapshot BOOLEAN DEFAULT false NOT NULL",
    ):
        assert column in sql

    for statement in (
        "ALTER TABLE codebases ADD COLUMN active_version_id VARCHAR(255)",
        "ALTER TABLE codebase_files ADD COLUMN version_id VARCHAR(255)",
        "ALTER TABLE codebase_symbols ADD COLUMN version_id VARCHAR(255)",
        "ALTER TABLE codebase_edges ADD COLUMN version_id VARCHAR(255)",
        "ALTER TABLE codebase_chunks ADD COLUMN version_id VARCHAR(255)",
        "ALTER TABLE codebase_artifacts ADD COLUMN version_id VARCHAR(255)",
        "ALTER TABLE codebase_symbols ADD COLUMN qualified_name VARCHAR(1024)",
        "ALTER TABLE codebase_symbols ADD COLUMN parser VARCHAR(64)",
        "ALTER TABLE codebase_symbols ADD COLUMN confidence FLOAT",
        "ALTER TABLE codebase_edges ADD COLUMN resolution VARCHAR(64)",
        "ALTER TABLE codebase_edges ADD COLUMN confidence FLOAT",
        "ALTER TABLE codebase_edges ADD COLUMN evidence JSONB",
        "ALTER TABLE codebase_chunks ADD COLUMN search_text TEXT",
    ):
        assert statement in sql

    assert "ALTER TABLE codebase_chunks ADD COLUMN search_vector tsvector" in sql
    assert "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_codebase_files_version_path" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_codebase_symbols_version_qualified_name" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_codebase_edges_version_src" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_codebase_edges_version_dst" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_codebase_chunks_search_vector" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_codebase_chunks_version" in sql


def test_upgrade_backfills_legacy_v1_versions_rows_and_bindings():
    sql = _offline_sql("upgrade")

    assert "md5('codebase-version:v1:' || cb.id)" in sql
    assert "INSERT INTO codebase_versions" in sql
    assert "legacy_snapshot" in sql
    assert "source_snapshot_key" in sql
    assert "cb.snapshot_key" in sql
    assert "vector_search', NOT cb.vector_degraded" in sql
    assert "UPDATE codebases AS target SET active_version_id" in sql

    for table in (
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
    ):
        assert f"UPDATE {table} AS target" in sql
        assert (
            "SET version_id = "
            "md5('codebase-version:v1:' || target.codebase_id)"
            in sql
        )

    assert "INSERT INTO session_resource_bindings" in sql
    assert "'codebase'" in sql
    assert "'migration:e9f0a1b2c3d4'" in sql
    assert "'session-codebase-binding:e9:'" in sql
    assert "session_row.codebase_id" in sql


def test_downgrade_removes_backfill_bindings_and_version_columns():
    sql = _offline_sql("downgrade")

    assert "DELETE FROM session_resource_bindings AS marker" in sql
    assert "marker.bound_by = 'migration:e9f0a1b2c3d4'" in sql
    assert "DROP TABLE codebase_versions" in sql
    for statement in (
        "ALTER TABLE codebases DROP COLUMN active_version_id",
        "ALTER TABLE codebase_files DROP COLUMN version_id",
        "ALTER TABLE codebase_symbols DROP COLUMN qualified_name",
        "ALTER TABLE codebase_edges DROP COLUMN evidence",
        "ALTER TABLE codebase_chunks DROP COLUMN search_vector",
    ):
        assert statement in sql
