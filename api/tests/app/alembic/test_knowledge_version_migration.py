#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Contract tests for immutable knowledge-base version storage and v1 backfill."""
import importlib.util
import inspect
import logging
import operator
import os
import threading
import uuid
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory

from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
)
from app.infrastructure.models.base import Base
from core.config import get_settings


MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "c7d8e9f0a1b2_add_knowledge_base_versions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "knowledge_version_migration", MIGRATION_PATH
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


def _position(sql: str, statement: str) -> int:
    assert statement in sql
    return sql.index(statement)


def test_c7_remains_linear_between_b8_and_gc_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(script.get_heads()) == 1, "migration chain must stay linear"
    assert script.get_revision("c7d8e9f0a1b2").down_revision == "b8d9e0f1a2b3"
    assert script.get_revision("d8e9f0a1b2c3").down_revision == "c7d8e9f0a1b2"


def test_upgrade_creates_immutable_version_revision_and_manifest_tables():
    sql = _offline_sql("upgrade")

    for table in (
        "knowledge_base_versions",
        "knowledge_document_revisions",
        "knowledge_base_version_documents",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert (
        "FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_bases (id) "
        "ON DELETE CASCADE" in sql
    )
    assert (
        "FOREIGN KEY(parent_version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id)" in sql
    )
    assert (
        "FOREIGN KEY(build_id) REFERENCES resource_builds (id) ON DELETE SET NULL"
        in sql
    )
    assert (
        "FOREIGN KEY(document_revision_id, document_id) REFERENCES "
        "knowledge_document_revisions (id, document_id) ON DELETE RESTRICT"
        in sql
    )
    assert (
        sql.count(
            "REFERENCES knowledge_documents (id"
        )
        == 2
    )
    assert "UNIQUE (version_id, document_id)" in sql

    assert "capabilities JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    assert "degraded_reasons JSONB DEFAULT '[]'::jsonb NOT NULL" in sql
    assert "metrics JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    assert "legacy_snapshot BOOLEAN DEFAULT false NOT NULL" in sql
    assert "parsed_blocks JSONB DEFAULT '[]'::jsonb NOT NULL" in sql
    assert "needs_chunk_clone BOOLEAN DEFAULT false NOT NULL" in sql


def test_upgrade_backfills_deterministic_versions_revisions_manifests_and_rows():
    sql = _offline_sql("upgrade")

    assert "md5('knowledge-base-version:v1:' || kb.id)" in sql
    assert "md5('knowledge-document-revision:v1:' || doc.id)" in sql
    assert "legacy_snapshot" in sql
    assert "true" in sql
    assert "INSERT INTO knowledge_base_versions" in sql
    assert "INSERT INTO knowledge_document_revisions" in sql
    assert "needs_chunk_clone" in sql
    assert "doc.status = 'ready' AND EXISTS" in sql
    assert "legacy_chunk.level = 'child'" in sql
    assert "INSERT INTO knowledge_base_version_documents" in sql
    assert (
        "UPDATE knowledge_bases AS target SET active_version_id = "
        "md5('knowledge-base-version:v1:' || target.id)" in sql
    )
    for table in (
        "knowledge_chunks",
        "knowledge_entities",
        "knowledge_relations",
        "knowledge_entity_refs",
    ):
        assert f"UPDATE {table} AS target" in sql
        assert (
            "SET version_id = "
            "md5('knowledge-base-version:v1:' || target.kb_id)"
            in sql
        )

    assert "lower(btrim(target.name))" in sql
    assert "earlier.id < target.id" in sql
    assert "'#legacy-'" in sql


def test_p1_1_c7_is_expand_compatible_and_defers_contract_nullability():
    sql = _offline_sql("upgrade")

    for table in (
        "knowledge_chunks",
        "knowledge_entities",
        "knowledge_relations",
        "knowledge_entity_refs",
    ):
        assert f"ALTER TABLE {table} ALTER COLUMN version_id SET NOT NULL" not in sql
        assert Base.metadata.tables[table].c.version_id.nullable is True
    assert "ALTER COLUMN normalized_name SET NOT NULL" not in sql
    assert Base.metadata.tables["knowledge_entities"].c.normalized_name.nullable is True

    migration = _load_migration()
    assert migration.CONTRACT_MIGRATION_GATE == (
        "all KB writers explicitly supply candidate version_id and "
        "normalized_name; backfill null counts are zero; composite FKs are "
        "validated"
    )
    assert "DEFAULT" not in next(
        statement
        for statement in sql.split(";")
        if "knowledge_chunks ADD COLUMN version_id" in statement
    )


def test_p1_2_upgrade_adds_composite_ownership_and_closure_constraints():
    sql = _offline_sql("upgrade")

    assert (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_kb_entities_version_normalized_name_type "
        "ON knowledge_entities (version_id, normalized_name, type)" in sql
    )
    assert (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_kb_chunks_version_doc "
        "ON knowledge_chunks (version_id, doc_id)" in sql
    )
    assert (
        "FOREIGN KEY(active_version_id, id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id)" in sql
    )
    assert (
        "FOREIGN KEY(parent_version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, knowledge_base_id) REFERENCES "
        "knowledge_base_versions (id, knowledge_base_id)" in sql
    )
    assert (
        "FOREIGN KEY(document_revision_id, document_id) REFERENCES "
        "knowledge_document_revisions (id, document_id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, doc_id) REFERENCES "
        "knowledge_base_version_documents (version_id, document_id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, src_entity_id) REFERENCES "
        "knowledge_entities (version_id, id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, dst_entity_id) REFERENCES "
        "knowledge_entities (version_id, id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, chunk_id) REFERENCES "
        "knowledge_chunks (version_id, id)" in sql
    )
    assert (
        "FOREIGN KEY(version_id, entity_id) REFERENCES "
        "knowledge_entities (version_id, id)" in sql
    )


def test_p1_3_binding_backfill_preserves_history_and_downgrade_restores_b8():
    upgrade_sql = _offline_sql("upgrade")
    downgrade_sql = _offline_sql("downgrade")

    assert "UPDATE session_resource_bindings AS previous_binding" in upgrade_sql
    assert "SET is_current = false" in upgrade_sql
    assert "supersedes_binding_id" in upgrade_sql
    assert "'migration:c7d8e9f0a1b2'" in upgrade_sql
    assert "'session-knowledge-base-binding:c7:'" in upgrade_sql
    assert "session_row.id" in upgrade_sql
    assert "DELETE FROM session_resource_bindings AS marker" in downgrade_sql
    assert "marker.bound_by = 'migration:c7d8e9f0a1b2'" in downgrade_sql
    assert "UPDATE session_resource_bindings AS previous_binding" in downgrade_sql
    assert "SET is_current = true" in downgrade_sql


def test_p1_4_backfill_is_bounded_resumable_and_large_indexes_are_concurrent():
    sql = _offline_sql("upgrade")

    assert "LIMIT 10000" in sql
    assert "SKIP LOCKED" in sql
    assert "target.version_id IS NULL" in sql
    assert "c7 backfill" in sql
    assert "COMMIT;" in sql
    for index in (
        "ix_kb_chunks_version_doc",
        "uq_kb_chunks_version_id",
        "ix_kb_entities_version_name",
        "uq_kb_entities_version_id",
        "uq_kb_entities_version_normalized_name_type",
        "ix_kb_relations_version_src",
        "ix_kb_relations_version_dst",
        "ix_kb_entity_refs_version_doc",
        "ix_kb_entity_refs_version_entity",
    ):
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index}" in sql or (
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {index}" in sql
        )


def test_fix2_red_p1_4_revision_has_guards_and_invalid_index_recovery():
    migration = _load_migration()
    upgrade_source = inspect.getsource(migration.upgrade)
    downgrade_source = inspect.getsource(migration.downgrade)

    assert upgrade_source.count("_add_column_if_missing(") == 6
    assert upgrade_source.count("_create_table_if_missing(") == 3
    assert "_get_index_validity" in inspect.getsource(
        migration._create_concurrent_indexes
    )
    assert "DROP INDEX CONCURRENTLY" in inspect.getsource(
        migration._create_concurrent_indexes
    )
    assert "IF NOT EXISTS" in inspect.getsource(migration._add_not_valid_fk)
    assert "DROP CONSTRAINT IF EXISTS" in downgrade_source
    assert "DROP COLUMN IF EXISTS" in downgrade_source
    assert "DROP TABLE IF EXISTS" in downgrade_source


def test_fix2_red_p1_4_downgrade_binding_restore_is_bounded_and_resumable():
    sql = _offline_sql("downgrade")

    assert "c7_restore_binding_batch" in sql
    assert "LIMIT requested_batch_size" in sql
    assert "c7_restore_binding_batch(10000)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "c7 restore bindings: batch %%, total %%" in sql
    assert "DROP FUNCTION pg_temp.c7_restore_binding_batch(integer)" in sql


def test_fix2_red_live_revision_fault_recovery_matrix_is_collected():
    required = {
        "test_postgres_revision_upgrade_fault_invalid_index_and_unstamped_rerun",
        "test_postgres_revision_downgrade_bounded_fault_and_rerun",
        "test_postgres_legacy_normalized_name_collision_is_deterministic",
    }
    assert required <= {
        name
        for name, value in globals().items()
        if name.startswith("test_postgres_") and callable(value)
    }


def test_fix2_red_legacy_normalized_names_avoid_reserved_suffix_collisions():
    sql = _offline_sql("upgrade")

    assert "~ '#legacy-[0-9a-f]{32}$'" in sql
    assert "left(lower(btrim(target.name)), 470)" in sql
    assert "md5(target.id)" in sql


def test_downgrade_removes_dependents_before_version_tables_and_preserves_legacy_columns():
    sql = _offline_sql("downgrade")

    active_fk = _position(
        sql,
        "ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS "
        "fk_knowledge_bases_active_version_owner",
    )
    active_column = _position(
        sql,
        "ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS active_version_id",
    )
    manifest_table = _position(
        sql, "DROP TABLE IF EXISTS knowledge_base_version_documents"
    )
    revision_table = _position(
        sql, "DROP TABLE IF EXISTS knowledge_document_revisions"
    )
    version_table = _position(
        sql, "DROP TABLE IF EXISTS knowledge_base_versions"
    )

    assert active_fk < active_column < manifest_table < revision_table < version_table
    assert "DROP COLUMN knowledge_base_id" not in sql
    assert "DROP COLUMN legacy_v1_migrated" not in sql
    assert "DROP TABLE knowledge_bases" not in sql
    assert "DROP TABLE knowledge_documents" not in sql


def test_p2_1_cyclic_orm_constraints_are_named_and_drop_all_is_sortable():
    statements: list[str] = []
    engine = sa.create_mock_engine(
        "postgresql+psycopg2://",
        lambda statement, *_args, **_kwargs: statements.append(
            str(statement.compile(dialect=engine.dialect))
        ),
    )

    Base.metadata.create_all(engine)
    Base.metadata.drop_all(engine)

    active_fk = next(
        constraint
        for constraint in Base.metadata.tables["knowledge_bases"].constraints
        if getattr(constraint, "name", None)
        == "fk_knowledge_bases_active_version_owner"
    )
    assert active_fk.use_alter is True
    assert any(
        "DROP CONSTRAINT fk_knowledge_bases_active_version_owner" in statement
        for statement in statements
    )


def test_p2_2_nested_domain_values_are_defensively_deeply_immutable():
    capabilities = {"keyword_search": True}
    metrics = {"counts": {"chunks": 1}, "warnings": ["legacy"]}
    parsed_blocks = [{"text": "before"}]
    version = KnowledgeBaseVersion(
        knowledge_base_id="kb-1",
        capabilities=capabilities,
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
        metrics=metrics,
    )
    revision = KnowledgeDocumentRevision(
        document_id="doc-1",
        source_digest="digest",
        parsed_blocks=parsed_blocks,
    )

    capabilities["keyword_search"] = False
    metrics["counts"]["chunks"] = 2
    parsed_blocks[0]["text"] = "after"
    assert version.capabilities["keyword_search"] is True
    assert version.metrics["counts"]["chunks"] == 1
    assert revision.parsed_blocks[0]["text"] == "before"

    with pytest.raises(TypeError):
        version.capabilities["graph_search"] = False
    with pytest.raises((AttributeError, TypeError)):
        version.degraded_reasons.append("GRAPH_UNAVAILABLE")
    with pytest.raises(TypeError):
        version.metrics["counts"]["chunks"] = 3
    with pytest.raises(TypeError):
        revision.parsed_blocks[0]["text"] = "mutated"


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: operator.setitem(value, "escape", 1),
        lambda value: value.update({"escape": 1}),
        lambda value: value.setdefault("escape", 1),
        lambda value: value.pop("counts"),
        lambda value: value.popitem(),
        lambda value: value.clear(),
        lambda value: operator.delitem(value, "counts"),
        lambda value: operator.ior(value, {"escape": 1}),
        lambda value: dict.__setitem__(value, "escape", 1),
    ),
)
def test_fix2_red_p2_2_all_mapping_mutators_leave_original_unchanged(
    mutator,
):
    version = KnowledgeBaseVersion(
        knowledge_base_id="kb-immutable",
        metrics={"counts": {"chunks": 1}},
    )
    original_bytes = version.model_dump_json().encode()
    original_value = {"counts": {"chunks": 1}}

    assert not isinstance(version.metrics, dict)
    with pytest.raises((AttributeError, TypeError)):
        mutator(version.metrics)
    assert version.model_dump_json().encode() == original_bytes
    assert version.metrics == original_value


def test_p2_3_schema_reuses_legacy_hnsw_and_declares_runtime_version_strategy():
    migration = _load_migration()
    sql = _offline_sql("upgrade")

    assert "ix_kb_chunks_version_embedding" not in sql
    assert "USING hnsw" not in sql
    assert migration.VECTOR_VERSION_FILTER_STRATEGY == (
        "reuse ix_kb_chunks_embedding; filter by version_id btree; "
        "Task 6 retriever must enable pgvector iterative_scan or introduce "
        "version partitions before filtered ANN is production-enabled"
    )


def test_p2_4_live_postgres_matrix_covers_all_reviewed_failure_modes():
    required = {
        "test_postgres_expand_writer_compatibility_and_composite_closure",
        "test_postgres_binding_downgrade_and_deterministic_reupgrade",
        "test_postgres_batched_backfill_concurrent_access_and_index_evidence",
    }
    assert required <= {
        name
        for name, value in globals().items()
        if name.startswith("test_postgres_") and callable(value)
    }


POSTGRES_INTEGRATION = pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for live PostgreSQL "
        "migration proofs"
    ),
)


@contextmanager
def _legacy_postgres_schema(*, chunk_count: int = 1):
    """Create the exact b8-facing KB shape in a disposable PostgreSQL schema."""
    uri = str(get_settings().sqlalchemy_database_uri).replace(
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
    )
    engine = sa.create_engine(uri, connect_args={"connect_timeout": 3})
    schema = f"kb_version_migration_{uuid.uuid4().hex}"
    connection = None
    schema_created = False
    try:
        connection = engine.connect()
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        connection.execute(
            sa.text(f'SET search_path TO "{schema}", public')
        )
        connection.commit()
        for statement in (
            """
            CREATE TABLE alembic_version (
                version_num varchar(32) NOT NULL
            )
            """,
            """
            INSERT INTO alembic_version (version_num)
            VALUES ('b8d9e0f1a2b3')
            """,
            "CREATE TABLE resource_builds (id varchar(255) PRIMARY KEY)",
            """
            CREATE TABLE knowledge_bases (
                id varchar(255) PRIMARY KEY,
                status varchar(32) NOT NULL,
                vector_degraded boolean NOT NULL DEFAULT false,
                legacy_v1_migrated boolean NOT NULL DEFAULT false,
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE knowledge_documents (
                id varchar(255) PRIMARY KEY,
                kb_id varchar(255) NOT NULL REFERENCES knowledge_bases(id),
                source_ref text NOT NULL DEFAULT '',
                file_id varchar(255),
                page_count integer NOT NULL DEFAULT 0,
                status varchar(32) NOT NULL,
                error text,
                warning text,
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE knowledge_chunks (
                id varchar(255) PRIMARY KEY,
                kb_id varchar(255) NOT NULL REFERENCES knowledge_bases(id),
                doc_id varchar(255) NOT NULL REFERENCES knowledge_documents(id),
                embedding real[]
            )
            """,
            """
            CREATE INDEX ix_kb_chunks_embedding
            ON knowledge_chunks (id)
            """,
            """
            CREATE TABLE knowledge_entities (
                id varchar(255) PRIMARY KEY,
                kb_id varchar(255) NOT NULL REFERENCES knowledge_bases(id),
                name varchar(512) NOT NULL,
                type varchar(128) NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE knowledge_relations (
                id varchar(255) PRIMARY KEY,
                kb_id varchar(255) NOT NULL REFERENCES knowledge_bases(id),
                src_entity_id varchar(255) NOT NULL,
                dst_entity_id varchar(255) NOT NULL,
                chunk_id varchar(255)
            )
            """,
            """
            CREATE TABLE knowledge_entity_refs (
                id varchar(255) PRIMARY KEY,
                kb_id varchar(255) NOT NULL REFERENCES knowledge_bases(id),
                entity_id varchar(255) NOT NULL,
                doc_id varchar(255) NOT NULL
            )
            """,
            """
            CREATE TABLE sessions (
                id varchar(255) PRIMARY KEY,
                knowledge_base_id varchar(255),
                owner_user_id varchar(255),
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE session_resource_bindings (
                id varchar(255) PRIMARY KEY,
                session_id varchar(255) NOT NULL,
                resource_kind varchar(64) NOT NULL,
                resource_id varchar(255) NOT NULL,
                version_id varchar(255) NOT NULL,
                is_current boolean NOT NULL DEFAULT true,
                supersedes_binding_id varchar(255)
                    REFERENCES session_resource_bindings(id)
                    ON DELETE SET NULL,
                bound_by varchar(255) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE UNIQUE INDEX uq_session_resource_bindings_current
            ON session_resource_bindings(session_id, resource_kind)
            WHERE is_current = true
            """,
            """
            INSERT INTO knowledge_bases (
                id, status, vector_degraded, legacy_v1_migrated
            ) VALUES
                ('kb-a', 'ready', false, true),
                ('kb-b', 'pending', true, false)
            """,
            """
            INSERT INTO knowledge_documents (
                id, kb_id, source_ref, file_id, page_count, status
            ) VALUES
                ('doc-a', 'kb-a', 'upload/a.pdf', 'file-a', 7, 'ready'),
                ('doc-b', 'kb-b', 'upload/b.pdf', 'file-b', 2, 'pending')
            """,
            """
            INSERT INTO knowledge_chunks (id, kb_id, doc_id)
            VALUES
                ('chunk-a', 'kb-a', 'doc-a'),
                ('chunk-b', 'kb-b', 'doc-b')
            """,
            """
            INSERT INTO knowledge_entities (id, kb_id, name, type)
            VALUES
                ('entity-a', 'kb-a', ' OpenCitadel ', 'project'),
                ('entity-b', 'kb-b', 'Other', 'project')
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, chunk_id
            ) VALUES
                ('relation-a', 'kb-a', 'entity-a', 'entity-a', 'chunk-a'),
                ('relation-b', 'kb-b', 'entity-b', 'entity-b', 'chunk-b')
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id
            ) VALUES
                ('ref-a', 'kb-a', 'entity-a', 'doc-a'),
                ('ref-b', 'kb-b', 'entity-b', 'doc-b')
            """,
            """
            INSERT INTO sessions (
                id, knowledge_base_id, owner_user_id
            ) VALUES
                ('session-1', 'kb-a', 'user-1'),
                ('session-2', 'kb-b', NULL)
            """,
            """
            INSERT INTO session_resource_bindings (
                id, session_id, resource_kind, resource_id, version_id,
                is_current, bound_by
            ) VALUES (
                'legacy-binding', 'session-1', 'knowledge_base',
                'kb-a', 'legacy:kb-a', true, 'user-1'
            )
            """,
        ):
            connection.execute(sa.text(statement))
        if chunk_count > 1:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO knowledge_chunks (id, kb_id, doc_id)
                    SELECT
                        'chunk-bulk-' || sequence_no,
                        'kb-a',
                        'doc-a'
                    FROM generate_series(2, :chunk_count) AS sequence_no
                    """
                ),
                {"chunk_count": chunk_count},
            )
        connection.commit()
        yield engine, connection, schema
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        try:
            if schema_created:
                with engine.begin() as cleanup:
                    cleanup.execute(
                        sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    )
        finally:
            engine.dispose()


def _run_migration_action(connection, migration, action: str) -> None:
    connection.commit()
    context = MigrationContext.configure(connection)
    previous_op = migration.op
    migration.op = Operations(context)
    try:
        getattr(migration, action)()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        migration.op = previous_op


def _run_alembic_revision(
    connection,
    script: ScriptDirectory,
    destination: str,
) -> None:
    """Run and stamp the real revision step on the supplied test connection."""
    config = Config("alembic.ini")
    current = connection.scalar(
        sa.text("SELECT version_num FROM alembic_version")
    )
    connection.commit()
    if destination == "c7d8e9f0a1b2":
        revision_steps = script._upgrade_revs(destination, current)
    else:
        revision_steps = script._downgrade_revs(destination, current)

    def migration_steps(_heads, _context):
        return revision_steps

    with EnvironmentContext(
        config,
        script,
        fn=migration_steps,
        destination_rev=destination,
    ) as environment:
        environment.configure(connection=connection)
        with environment.begin_transaction():
            environment.run_migrations()
    connection.commit()


def _assert_integrity_error(connection, statement: str) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        connection.execute(sa.text(statement))
        connection.commit()
    connection.rollback()


@POSTGRES_INTEGRATION
def test_postgres_expand_writer_compatibility_and_composite_closure():
    migration = _load_migration()
    with _legacy_postgres_schema() as (_engine, connection, _schema):
        _run_migration_action(connection, migration, "upgrade")

        # A writer deployed before c7 can continue omitting all expand fields.
        for statement in (
            """
            INSERT INTO knowledge_chunks (id, kb_id, doc_id)
            VALUES ('legacy-writer-chunk', 'kb-a', 'doc-a')
            """,
            """
            INSERT INTO knowledge_entities (id, kb_id, name, type)
            VALUES ('legacy-writer-entity', 'kb-a', 'Legacy', 'concept')
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, chunk_id
            ) VALUES (
                'legacy-writer-relation', 'kb-a',
                'entity-a', 'entity-a', 'chunk-a'
            )
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id
            ) VALUES (
                'legacy-writer-ref', 'kb-a', 'entity-a', 'doc-a'
            )
            """,
        ):
            connection.execute(sa.text(statement))
        connection.commit()
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM knowledge_chunks
                WHERE id = 'legacy-writer-chunk' AND version_id IS NULL
                """
            )
        ) == 1
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM knowledge_entities
                WHERE id = 'legacy-writer-entity'
                  AND version_id IS NULL
                  AND normalized_name IS NULL
                """
            )
        ) == 1

        revision_a = connection.scalar(
            sa.text(
                "SELECT md5('knowledge-document-revision:v1:doc-a')"
            )
        )
        revision_b = connection.scalar(
            sa.text(
                "SELECT md5('knowledge-document-revision:v1:doc-b')"
            )
        )
        version_b = connection.scalar(
            sa.text("SELECT md5('knowledge-base-version:v1:kb-b')")
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_base_versions (
                    id, knowledge_base_id, state
                ) VALUES
                    ('candidate-a', 'kb-a', 'building'),
                    ('candidate-b', 'kb-b', 'building'),
                    ('candidate-a-revision-check', 'kb-a', 'building')
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_base_version_documents (
                    version_id, knowledge_base_id, document_id,
                    document_revision_id, ordinal, state
                ) VALUES
                    ('candidate-a', 'kb-a', 'doc-a', :revision_a, 0, 'indexed'),
                    ('candidate-b', 'kb-b', 'doc-b', :revision_b, 0, 'parsed')
                """
            ),
            {"revision_a": revision_a, "revision_b": revision_b},
        )
        for statement in (
            """
            INSERT INTO knowledge_chunks (
                id, kb_id, doc_id, version_id
            ) VALUES (
                'candidate-chunk-a', 'kb-a', 'doc-a', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_chunks (
                id, kb_id, doc_id, version_id
            ) VALUES (
                'candidate-chunk-b', 'kb-b', 'doc-b', 'candidate-b'
            )
            """,
            """
            INSERT INTO knowledge_entities (
                id, kb_id, name, type, version_id, normalized_name
            ) VALUES (
                'candidate-entity-a', 'kb-a', 'Candidate', 'concept',
                'candidate-a', 'candidate'
            )
            """,
            """
            INSERT INTO knowledge_entities (
                id, kb_id, name, type, version_id, normalized_name
            ) VALUES (
                'candidate-entity-b', 'kb-b', 'Candidate B', 'concept',
                'candidate-b', 'candidate-b'
            )
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, chunk_id, version_id
            ) VALUES (
                'candidate-relation-a', 'kb-a',
                'candidate-entity-a', 'candidate-entity-a',
                'candidate-chunk-a', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id, version_id
            ) VALUES (
                'candidate-ref-a', 'kb-a', 'candidate-entity-a',
                'doc-a', 'candidate-a'
            )
            """,
        ):
            connection.execute(sa.text(statement))
        connection.commit()

        # Every ownership/closure edge rejects a cross-version or cross-KB row.
        violations = (
            """
            UPDATE knowledge_bases
            SET active_version_id = 'candidate-b'
            WHERE id = 'kb-a'
            """,
            """
            INSERT INTO knowledge_base_versions (
                id, knowledge_base_id, parent_version_id
            ) VALUES ('bad-parent', 'kb-a', 'candidate-b')
            """,
            f"""
            INSERT INTO knowledge_base_version_documents (
                version_id, knowledge_base_id, document_id,
                document_revision_id, ordinal
            ) VALUES (
                'candidate-b', 'kb-a', 'doc-a', '{revision_a}', 1
            )
            """,
            f"""
            INSERT INTO knowledge_base_version_documents (
                version_id, knowledge_base_id, document_id,
                document_revision_id, ordinal
            ) VALUES (
                'candidate-a', 'kb-a', 'doc-b', '{revision_b}', 1
            )
            """,
            f"""
            INSERT INTO knowledge_base_version_documents (
                version_id, knowledge_base_id, document_id,
                document_revision_id, ordinal
            ) VALUES (
                'candidate-a-revision-check', 'kb-a',
                'doc-a', '{revision_b}', 0
            )
            """,
            """
            INSERT INTO knowledge_chunks (
                id, kb_id, doc_id, version_id
            ) VALUES ('bad-chunk-owner', 'kb-a', 'doc-b', 'candidate-b')
            """,
            """
            INSERT INTO knowledge_chunks (
                id, kb_id, doc_id, version_id
            ) VALUES ('bad-chunk-manifest', 'kb-a', 'doc-b', 'candidate-a')
            """,
            """
            INSERT INTO knowledge_entities (
                id, kb_id, name, type, version_id, normalized_name
            ) VALUES (
                'bad-entity-owner', 'kb-a', 'Bad', 'concept',
                'candidate-b', 'bad-owner'
            )
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, version_id
            ) VALUES (
                'bad-relation-src', 'kb-a', 'entity-b',
                'candidate-entity-a', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, version_id
            ) VALUES (
                'bad-relation-dst', 'kb-a', 'candidate-entity-a',
                'entity-b', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, chunk_id, version_id
            ) VALUES (
                'bad-relation-chunk', 'kb-a',
                'candidate-entity-a', 'candidate-entity-a',
                'chunk-b', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_relations (
                id, kb_id, src_entity_id, dst_entity_id, chunk_id, version_id
            ) VALUES (
                'bad-relation-owner', 'kb-a',
                'candidate-entity-b', 'candidate-entity-b',
                'candidate-chunk-b', 'candidate-b'
            )
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id, version_id
            ) VALUES (
                'bad-ref-doc', 'kb-a', 'candidate-entity-a',
                'doc-b', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id, version_id
            ) VALUES (
                'bad-ref-entity', 'kb-a', 'entity-b',
                'doc-a', 'candidate-a'
            )
            """,
            """
            INSERT INTO knowledge_entity_refs (
                id, kb_id, entity_id, doc_id, version_id
            ) VALUES (
                'bad-ref-owner', 'kb-a', 'candidate-entity-b',
                'doc-b', 'candidate-b'
            )
            """,
        )
        for violation in violations:
            _assert_integrity_error(connection, violation)
        assert version_b is not None


@POSTGRES_INTEGRATION
def test_postgres_binding_downgrade_and_deterministic_reupgrade():
    migration = _load_migration()
    with _legacy_postgres_schema() as (_engine, connection, _schema):
        marker_id = connection.scalar(
            sa.text(
                "SELECT md5('session-knowledge-base-binding:c7:session-1')"
            )
        )
        _run_migration_action(connection, migration, "upgrade")
        bindings = connection.execute(
            sa.text(
                """
                SELECT id, version_id, is_current, supersedes_binding_id,
                       bound_by
                FROM session_resource_bindings
                WHERE session_id = 'session-1'
                  AND resource_kind = 'knowledge_base'
                ORDER BY id
                """
            )
        ).all()
        assert bindings == [
            (
                marker_id,
                connection.scalar(
                    sa.text("SELECT md5('knowledge-base-version:v1:kb-a')")
                ),
                True,
                "legacy-binding",
                "migration:c7d8e9f0a1b2",
            ),
            (
                "legacy-binding",
                "legacy:kb-a",
                False,
                None,
                "user-1",
            ),
        ]
        session_2_marker = connection.execute(
            sa.text(
                """
                SELECT id, supersedes_binding_id, is_current
                FROM session_resource_bindings
                WHERE session_id = 'session-2'
                """
            )
        ).one()
        assert session_2_marker[1:] == (None, True)

        _run_migration_action(connection, migration, "downgrade")
        assert connection.scalar(
            sa.text("SELECT to_regclass('knowledge_base_versions')")
        ) is None
        assert connection.execute(
            sa.text(
                """
                SELECT id, resource_id, version_id, is_current,
                       supersedes_binding_id, bound_by
                FROM session_resource_bindings
                WHERE session_id = 'session-1'
                """
            )
        ).one() == (
            "legacy-binding",
            "kb-a",
            "legacy:kb-a",
            True,
            None,
            "user-1",
        )
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM session_resource_bindings
                WHERE session_id = 'session-2'
                """
            )
        ) == 0

        _run_migration_action(connection, migration, "upgrade")
        assert connection.scalar(
            sa.text(
                """
                SELECT id
                FROM session_resource_bindings
                WHERE session_id = 'session-1' AND is_current = true
                """
            )
        ) == marker_id
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM session_resource_bindings
                WHERE session_id = 'session-1'
                  AND resource_kind = 'knowledge_base'
                """
            )
        ) == 2


@POSTGRES_INTEGRATION
def test_postgres_batched_backfill_concurrent_access_and_index_evidence(
    caplog,
):
    migration = _load_migration()
    with _legacy_postgres_schema(chunk_count=1205) as (
        engine,
        connection,
        schema,
    ):
        old_batch_size = migration._BATCH_SIZE
        original_runner = migration._run_batched_sql
        backfill_ready = threading.Event()
        resume_backfill = threading.Event()
        worker_errors: list[BaseException] = []
        paused = False

        def pausing_runner(label: str, one_batch_sql: str) -> None:
            nonlocal paused
            if label == "knowledge_chunks" and not paused:
                paused = True
                backfill_ready.set()
                if not resume_backfill.wait(timeout=20):
                    raise TimeoutError("timed out waiting for concurrent probe")
            original_runner(label, one_batch_sql)

        def run_upgrade() -> None:
            try:
                with engine.connect() as worker_connection:
                    worker_connection.execute(
                        sa.text(f'SET search_path TO "{schema}", public')
                    )
                    worker_connection.commit()
                    _run_migration_action(
                        worker_connection,
                        migration,
                        "upgrade",
                    )
            except BaseException as exc:
                worker_errors.append(exc)

        migration._BATCH_SIZE = 200
        migration._run_batched_sql = pausing_runner
        caplog.set_level(
            logging.INFO,
            logger="alembic.c7_knowledge_versions",
        )
        worker = threading.Thread(target=run_upgrade, daemon=True)
        try:
            worker.start()
            assert backfill_ready.wait(timeout=20)
            assert connection.scalar(
                sa.text("SELECT count(*) FROM knowledge_chunks")
            ) == 1206
            connection.execute(
                sa.text(
                    """
                    INSERT INTO knowledge_chunks (id, kb_id, doc_id)
                    VALUES ('chunk-concurrent', 'kb-a', 'doc-a')
                    """
                )
            )
            connection.commit()
            assert connection.scalar(
                sa.text(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE relation = 'knowledge_chunks'::regclass
                      AND mode = 'AccessExclusiveLock'
                      AND granted
                    """
                )
            ) == 0
        finally:
            resume_backfill.set()
            worker.join(timeout=60)
            migration._run_batched_sql = original_runner
            migration._BATCH_SIZE = old_batch_size
        assert not worker.is_alive()
        if worker_errors:
            raise worker_errors[0]

        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM knowledge_chunks "
                "WHERE version_id IS NULL"
            )
        ) == 0
        assert any(
            "knowledge_chunks" in record.getMessage()
            and "batch 200" in record.getMessage()
            for record in caplog.records
        )

        expected_indexes = {
            name
            for name, _unique, _table, _expr
            in migration._CONCURRENT_INDEXES
        }
        index_rows = connection.execute(
            sa.text(
                """
                SELECT index_class.relname, index.indisvalid
                FROM pg_index AS index
                JOIN pg_class AS index_class
                  ON index_class.oid = index.indexrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = index_class.relnamespace
                WHERE index_class.relname::text =
                      ANY(CAST(:index_names AS text[]))
                  AND namespace.nspname = current_schema()
                """
            ),
            {"index_names": list(expected_indexes)},
        ).all()
        assert {name for name, _valid in index_rows} == expected_indexes
        assert all(valid for _name, valid in index_rows)

        version_a = connection.scalar(
            sa.text("SELECT md5('knowledge-base-version:v1:kb-a')")
        )
        connection.execute(sa.text("SET enable_seqscan = off"))
        explain = "\n".join(
            row[0]
            for row in connection.execute(
                sa.text(
                    """
                    EXPLAIN
                    SELECT id
                    FROM knowledge_chunks
                    WHERE version_id = :version_id AND doc_id = 'doc-a'
                    """
                ),
                {"version_id": version_a},
            )
        )
        connection.execute(sa.text("RESET enable_seqscan"))
        connection.commit()
        assert "ix_kb_chunks_version_doc" in explain

        connection.execute(
            sa.text(
                """
                UPDATE knowledge_chunks
                SET version_id = NULL
                WHERE id IN (
                    SELECT id FROM knowledge_chunks ORDER BY id LIMIT 3
                )
                """
            )
        )
        connection.commit()
        context = MigrationContext.configure(connection)
        previous_op = migration.op
        migration.op = Operations(context)
        try:
            migration._run_batched_sql(
                "resume proof",
                """
                WITH batch AS (
                    SELECT row.ctid
                    FROM knowledge_chunks AS row
                    WHERE row.version_id IS NULL
                    LIMIT 2
                    FOR UPDATE OF row SKIP LOCKED
                )
                UPDATE knowledge_chunks AS target
                SET version_id =
                    md5('knowledge-base-version:v1:' || target.kb_id)
                FROM batch
                WHERE target.ctid = batch.ctid
                  AND target.version_id IS NULL
                """,
            )
            connection.commit()
        finally:
            migration.op = previous_op
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM knowledge_chunks "
                "WHERE version_id IS NULL"
            )
        ) == 0


@POSTGRES_INTEGRATION
def test_postgres_revision_upgrade_fault_invalid_index_and_unstamped_rerun(
    monkeypatch,
):
    with _legacy_postgres_schema() as (engine, connection, schema):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        migration = script.get_revision("c7d8e9f0a1b2").module
        monkeypatch.setattr(migration, "_BATCH_SIZE", 1)
        original_info = migration._LOGGER.info
        fault_injected = False

        def fail_after_committed_chunk_batch(message, *args, **kwargs):
            nonlocal fault_injected
            original_info(message, *args, **kwargs)
            if (
                message == "c7 backfill %s: batch %d, total %d"
                and args[0] == "knowledge_chunks"
                and not fault_injected
            ):
                fault_injected = True
                raise RuntimeError("injected after committed chunk batch")

        monkeypatch.setattr(
            migration._LOGGER,
            "info",
            fail_after_committed_chunk_batch,
        )
        with pytest.raises(
            RuntimeError,
            match="injected after committed chunk batch",
        ):
            _run_alembic_revision(
                connection,
                script,
                "c7d8e9f0a1b2",
            )
        connection.rollback()
        assert fault_injected is True
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == "b8d9e0f1a2b3"
        assert connection.scalar(
            sa.text("SELECT to_regclass('knowledge_base_versions')")
        ) is not None
        document_owner_index_oid = connection.scalar(
            sa.text(
                "SELECT 'uq_knowledge_documents_id_kb'::regclass::oid"
            )
        )
        connection.commit()

        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as maintenance:
            maintenance.execute(
                sa.text(f'SET search_path TO "{schema}", public')
            )
            maintenance.execute(
                sa.text(
                    "DROP INDEX CONCURRENTLY uq_kb_chunks_version_id"
                )
            )
            with pytest.raises(sa.exc.IntegrityError):
                maintenance.execute(
                    sa.text(
                        """
                        CREATE UNIQUE INDEX CONCURRENTLY
                        uq_kb_chunks_version_id
                        ON knowledge_chunks ((1))
                        """
                    )
                )
            assert maintenance.scalar(
                sa.text(
                    """
                    SELECT index_state.indisvalid
                    FROM pg_index AS index_state
                    JOIN pg_class AS index_class
                      ON index_class.oid = index_state.indexrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = index_class.relnamespace
                    WHERE index_class.relname = 'uq_kb_chunks_version_id'
                      AND namespace.nspname = current_schema()
                    """
                )
            ) is False

        monkeypatch.setattr(migration._LOGGER, "info", original_info)
        _run_alembic_revision(
            connection,
            script,
            "c7d8e9f0a1b2",
        )
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == "c7d8e9f0a1b2"
        assert connection.scalar(
            sa.text(
                "SELECT 'uq_knowledge_documents_id_kb'::regclass::oid"
            )
        ) == document_owner_index_oid
        assert connection.scalar(
            sa.text(
                """
                SELECT index_state.indisvalid
                FROM pg_index AS index_state
                JOIN pg_class AS index_class
                  ON index_class.oid = index_state.indexrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = index_class.relnamespace
                WHERE index_class.relname = 'uq_kb_chunks_version_id'
                  AND namespace.nspname = current_schema()
                """
            )
        ) is True
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM knowledge_chunks "
                "WHERE version_id IS NULL"
            )
        ) == 0
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM session_resource_bindings
                WHERE bound_by = 'migration:c7d8e9f0a1b2'
                  AND is_current = true
                """
            )
        ) == 2
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM pg_constraint
                WHERE conname LIKE 'fk_knowledge_%'
                  AND NOT convalidated
                """
            )
        ) >= 11


@POSTGRES_INTEGRATION
def test_postgres_revision_downgrade_bounded_fault_and_rerun(
    monkeypatch,
):
    with _legacy_postgres_schema() as (_engine, connection, _schema):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        migration = script.get_revision("c7d8e9f0a1b2").module
        _run_alembic_revision(
            connection,
            script,
            "c7d8e9f0a1b2",
        )
        monkeypatch.setattr(migration, "_BATCH_SIZE", 1)
        original_info = migration._LOGGER.info
        fault_injected = False

        def fail_after_committed_restore_batch(message, *args, **kwargs):
            nonlocal fault_injected
            original_info(message, *args, **kwargs)
            if (
                message == "c7 restore bindings: batch %d, total %d"
                and args[0] == 1
                and not fault_injected
            ):
                fault_injected = True
                raise RuntimeError("injected after committed restore batch")

        monkeypatch.setattr(
            migration._LOGGER,
            "info",
            fail_after_committed_restore_batch,
        )
        with pytest.raises(
            RuntimeError,
            match="injected after committed restore batch",
        ):
            _run_alembic_revision(
                connection,
                script,
                "b8d9e0f1a2b3",
            )
        connection.rollback()
        assert fault_injected is True
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == "c7d8e9f0a1b2"
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM session_resource_bindings
                WHERE bound_by = 'migration:c7d8e9f0a1b2'
                """
            )
        ) == 1

        monkeypatch.setattr(migration._LOGGER, "info", original_info)
        _run_alembic_revision(
            connection,
            script,
            "b8d9e0f1a2b3",
        )
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == "b8d9e0f1a2b3"
        assert connection.scalar(
            sa.text("SELECT to_regclass('knowledge_base_versions')")
        ) is None
        assert connection.execute(
            sa.text(
                """
                SELECT id, resource_id, version_id, is_current, bound_by
                FROM session_resource_bindings
                WHERE session_id = 'session-1'
                """
            )
        ).one() == (
            "legacy-binding",
            "kb-a",
            "legacy:kb-a",
            True,
            "user-1",
        )
        assert connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM session_resource_bindings
                WHERE session_id = 'session-2'
                """
            )
        ) == 0


@POSTGRES_INTEGRATION
def test_postgres_legacy_normalized_name_collision_is_deterministic():
    with _legacy_postgres_schema() as (_engine, connection, _schema):
        duplicate_id = "collision-duplicate"
        natural_id = "collision-natural"
        duplicate_hash = connection.scalar(
            sa.text("SELECT md5(:entity_id)"),
            {"entity_id": duplicate_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_entities (id, kb_id, name, type)
                VALUES
                    ('collision-base', 'kb-a', 'Thing', 'collision'),
                    (:duplicate_id, 'kb-a', ' thing ', 'collision'),
                    (
                        :natural_id,
                        'kb-a',
                        :natural_name,
                        'collision'
                    )
                """
            ),
            {
                "duplicate_id": duplicate_id,
                "natural_id": natural_id,
                "natural_name": f"thing#legacy-{duplicate_hash}",
            },
        )
        connection.commit()
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        _run_alembic_revision(
            connection,
            script,
            "c7d8e9f0a1b2",
        )

        rows = dict(
            connection.execute(
                sa.text(
                    """
                    SELECT id, normalized_name
                    FROM knowledge_entities
                    WHERE type = 'collision'
                    """
                )
            ).all()
        )
        natural_hash = connection.scalar(
            sa.text("SELECT md5(:entity_id)"),
            {"entity_id": natural_id},
        )
        assert rows == {
            "collision-base": "thing",
            duplicate_id: f"thing#legacy-{duplicate_hash}",
            natural_id: (
                f"thing#legacy-{duplicate_hash}"
                f"#legacy-{natural_hash}"
            ),
        }
        assert len(set(rows.values())) == 3
        assert connection.scalar(
            sa.text(
                """
                SELECT index_state.indisvalid
                FROM pg_index AS index_state
                JOIN pg_class AS index_class
                  ON index_class.oid = index_state.indexrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = index_class.relnamespace
                WHERE index_class.relname =
                    'uq_kb_entities_version_normalized_name_type'
                  AND namespace.nspname = current_schema()
                """
            )
        ) is True
