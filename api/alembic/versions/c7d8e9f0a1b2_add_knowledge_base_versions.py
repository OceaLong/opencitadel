"""expand knowledge storage with immutable versions and legacy-v1 backfill

Revision ID: c7d8e9f0a1b2
Revises: b8d9e0f1a2b3
Create Date: 2026-07-29
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONTRACT_MIGRATION_GATE = (
    "all KB writers explicitly supply candidate version_id and "
    "normalized_name; backfill null counts are zero; composite FKs are "
    "validated"
)
VECTOR_VERSION_FILTER_STRATEGY = (
    "reuse ix_kb_chunks_embedding; filter by version_id btree; "
    "Task 6 retriever must enable pgvector iterative_scan or introduce "
    "version partitions before filtered ANN is production-enabled"
)

_BATCH_SIZE = 10_000
_MIGRATION_ACTOR = "migration:c7d8e9f0a1b2"
_LOGGER = logging.getLogger("alembic.c7_knowledge_versions")

_CONCURRENT_INDEXES: tuple[tuple[str, bool, str, str], ...] = (
    (
        "uq_knowledge_documents_id_kb",
        True,
        "knowledge_documents",
        "(id, kb_id)",
    ),
    (
        "ix_kb_chunks_version_doc",
        False,
        "knowledge_chunks",
        "(version_id, doc_id)",
    ),
    (
        "ix_kb_chunks_version",
        False,
        "knowledge_chunks",
        "(version_id)",
    ),
    (
        "uq_kb_chunks_version_id",
        True,
        "knowledge_chunks",
        "(version_id, id)",
    ),
    (
        "ix_kb_entities_version_name",
        False,
        "knowledge_entities",
        "(version_id, normalized_name)",
    ),
    (
        "uq_kb_entities_version_id",
        True,
        "knowledge_entities",
        "(version_id, id)",
    ),
    (
        "uq_kb_entities_version_normalized_name_type",
        True,
        "knowledge_entities",
        "(version_id, normalized_name, type)",
    ),
    (
        "ix_kb_relations_version_src",
        False,
        "knowledge_relations",
        "(version_id, src_entity_id)",
    ),
    (
        "ix_kb_relations_version_dst",
        False,
        "knowledge_relations",
        "(version_id, dst_entity_id)",
    ),
    (
        "ix_kb_entity_refs_version_doc",
        False,
        "knowledge_entity_refs",
        "(version_id, doc_id)",
    ),
    (
        "ix_kb_entity_refs_version_entity",
        False,
        "knowledge_entity_refs",
        "(version_id, entity_id)",
    ),
)


def _add_column_if_missing(
    table_name: str,
    column: sa.Column,
) -> None:
    """Add an expand column once, including after an unstamped partial run."""
    context = op.get_context()
    if not context.as_sql:
        columns = {
            item["name"]
            for item in sa.inspect(op.get_bind()).get_columns(table_name)
        }
        if column.name in columns:
            return
    op.add_column(table_name, column)


def _create_table_if_missing(table_name: str) -> bool:
    """Return whether this run must execute an atomic CREATE TABLE."""
    context = op.get_context()
    if context.as_sql:
        return True
    return not sa.inspect(op.get_bind()).has_table(table_name)


def _create_index_if_missing(
    *,
    name: str,
    table: str,
    expression: str,
    unique: bool = False,
) -> None:
    qualifier = "UNIQUE " if unique else ""
    op.execute(
        sa.text(
            f"CREATE {qualifier}INDEX IF NOT EXISTS "
            f"{name} ON {table} {expression}"
        )
    )


def _get_index_validity(name: str) -> Union[bool, None]:
    """Return None/missing, False/invalid, or True/valid in current schema."""
    context = op.get_context()
    if context.as_sql:
        return None
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT index_state.indisvalid
            FROM pg_index AS index_state
            JOIN pg_class AS index_class
              ON index_class.oid = index_state.indexrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = index_class.relnamespace
            WHERE index_class.relname = :index_name
              AND namespace.nspname = current_schema()
            """
        ),
        {"index_name": name},
    ).first()
    return None if row is None else bool(row[0])


def _run_batched_sql(label: str, one_batch_sql: str) -> None:
    """Commit one bounded, idempotent batch at a time in online mode.

    NULL/missing target predicates are the durable cursor. Retrying the
    revision or the generated backfill body continues from remaining rows.
    Offline mode emits an equivalent valid PL/pgSQL loop because it cannot
    inspect client-side row counts while generating SQL.
    """
    context = op.get_context()
    statement = one_batch_sql.strip().rstrip(";")
    if context.as_sql:
        escaped_label = label.replace("'", "''")
        op.execute(
            sa.text(
                f"""
                DO $c7_backfill$
                DECLARE
                    batch_count integer := 0;
                    total_count bigint := 0;
                BEGIN
                    LOOP
                        {statement};
                        GET DIAGNOSTICS batch_count = ROW_COUNT;
                        total_count := total_count + batch_count;
                        RAISE NOTICE
                            'c7 backfill {escaped_label}: batch %, total %',
                            batch_count,
                            total_count;
                        EXIT WHEN batch_count = 0;
                    END LOOP;
                END
                $c7_backfill$
                """
            )
        )
        return

    total = 0
    with context.autocommit_block():
        bind = op.get_bind()
        while True:
            result = bind.execute(sa.text(statement))
            batch_count = max(result.rowcount or 0, 0)
            total += batch_count
            _LOGGER.info(
                "c7 backfill %s: batch %d, total %d",
                label,
                batch_count,
                total,
            )
            if batch_count == 0:
                break


def _create_concurrent_indexes() -> None:
    context = op.get_context()
    for name, unique, table, expression in _CONCURRENT_INDEXES:
        qualifier = "UNIQUE " if unique else ""
        validity = _get_index_validity(name)
        if validity is True:
            continue
        if validity is False:
            with context.autocommit_block():
                op.execute(
                    sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                )
        with context.autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE {qualifier}INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{name} ON {table} {expression}"
                )
            )


def _drop_concurrent_indexes() -> None:
    context = op.get_context()
    for name, _unique, _table, _expression in reversed(_CONCURRENT_INDEXES):
        with context.autocommit_block():
            op.execute(
                sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            )


def _create_binding_batch_function() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION pg_temp.c7_backfill_binding_batch(
                requested_batch_size integer
            )
            RETURNS integer
            LANGUAGE plpgsql
            AS $c7_binding$
            DECLARE
                candidate record;
                previous_id varchar(255);
                marker_id varchar(255);
                processed integer := 0;
            BEGIN
                FOR candidate IN
                    SELECT
                        session_row.id AS session_id,
                        session_row.knowledge_base_id AS resource_id,
                        session_row.owner_user_id AS owner_user_id,
                        session_row.created_at AS session_created_at,
                        kb.active_version_id AS version_id
                    FROM sessions AS session_row
                    JOIN knowledge_bases AS kb
                      ON kb.id = session_row.knowledge_base_id
                    WHERE session_row.knowledge_base_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM session_resource_bindings AS marker
                          WHERE marker.id = md5(
                              'session-knowledge-base-binding:c7:' || session_row.id
                          )
                            AND marker.bound_by = '{_MIGRATION_ACTOR}'
                      )
                    ORDER BY session_row.id
                    LIMIT requested_batch_size
                    FOR UPDATE OF session_row SKIP LOCKED
                LOOP
                    marker_id := md5(
                        'session-knowledge-base-binding:c7:' ||
                        candidate.session_id
                    );
                    previous_id := NULL;
                    SELECT binding.id
                    INTO previous_id
                    FROM session_resource_bindings AS binding
                    WHERE binding.session_id = candidate.session_id
                      AND binding.resource_kind = 'knowledge_base'
                      AND binding.is_current = true
                    FOR UPDATE;

                    INSERT INTO session_resource_bindings (
                        id,
                        session_id,
                        resource_kind,
                        resource_id,
                        version_id,
                        is_current,
                        supersedes_binding_id,
                        bound_by,
                        created_at
                    )
                    VALUES (
                        marker_id,
                        candidate.session_id,
                        'knowledge_base',
                        candidate.resource_id,
                        candidate.version_id,
                        false,
                        previous_id,
                        '{_MIGRATION_ACTOR}',
                        COALESCE(
                            candidate.session_created_at,
                            CURRENT_TIMESTAMP
                        )
                    )
                    ON CONFLICT (id) DO NOTHING;

                    IF previous_id IS NOT NULL THEN
                        UPDATE session_resource_bindings AS previous_binding
                        SET is_current = false
                        WHERE previous_binding.id = previous_id;
                    END IF;

                    UPDATE session_resource_bindings AS marker
                    SET is_current = true
                    WHERE marker.id = marker_id
                      AND marker.bound_by = '{_MIGRATION_ACTOR}';
                    processed := processed + 1;
                END LOOP;
                RETURN processed;
            END
            $c7_binding$
            """
        )
    )


def _backfill_bindings() -> None:
    context = op.get_context()
    if context.as_sql:
        _create_binding_batch_function()
        op.execute(
            sa.text(
                """
                DO $c7_binding_loop$
                DECLARE
                    batch_count integer := 0;
                    total_count bigint := 0;
                BEGIN
                    LOOP
                        SELECT pg_temp.c7_backfill_binding_batch(10000)
                        INTO batch_count;
                        total_count := total_count + batch_count;
                        RAISE NOTICE
                            'c7 backfill bindings: batch %, total %',
                            batch_count,
                            total_count;
                        EXIT WHEN batch_count = 0;
                    END LOOP;
                END
                $c7_binding_loop$
                """
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION pg_temp.c7_backfill_binding_batch(integer)"
            )
        )
        return

    with context.autocommit_block():
        _create_binding_batch_function()
        bind = op.get_bind()
        total = 0
        while True:
            batch_count = bind.scalar(
                sa.text(
                    "SELECT pg_temp.c7_backfill_binding_batch(:batch_size)"
                ),
                {"batch_size": _BATCH_SIZE},
            )
            total += int(batch_count or 0)
            _LOGGER.info(
                "c7 backfill bindings: batch %d, total %d",
                batch_count,
                total,
            )
            if not batch_count:
                break
        bind.execute(
            sa.text(
                "DROP FUNCTION pg_temp.c7_backfill_binding_batch(integer)"
            )
        )


def _create_binding_restore_batch_function() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION pg_temp.c7_restore_binding_batch(
                requested_batch_size integer
            )
            RETURNS integer
            LANGUAGE plpgsql
            AS $c7_binding_restore$
            DECLARE
                marker_row record;
                processed integer := 0;
            BEGIN
                FOR marker_row IN
                    SELECT id, session_id, supersedes_binding_id
                    FROM session_resource_bindings
                    WHERE bound_by = '{_MIGRATION_ACTOR}'
                    ORDER BY id
                    LIMIT requested_batch_size
                    FOR UPDATE SKIP LOCKED
                LOOP
                    DELETE FROM session_resource_bindings AS marker
                    WHERE marker.id = marker_row.id
                      AND marker.bound_by = '{_MIGRATION_ACTOR}';

                    IF marker_row.supersedes_binding_id IS NOT NULL
                       AND NOT EXISTS (
                           SELECT 1
                           FROM session_resource_bindings AS current_binding
                           WHERE current_binding.session_id =
                               marker_row.session_id
                             AND current_binding.resource_kind =
                                 'knowledge_base'
                             AND current_binding.is_current = true
                       )
                    THEN
                        UPDATE session_resource_bindings AS previous_binding
                        SET is_current = true
                        WHERE previous_binding.id =
                            marker_row.supersedes_binding_id;
                    END IF;
                    processed := processed + 1;
                END LOOP;
                RETURN processed;
            END
            $c7_binding_restore$
            """
        )
    )


def _restore_legacy_bindings() -> None:
    context = op.get_context()
    if context.as_sql:
        _create_binding_restore_batch_function()
        op.execute(
            sa.text(
                """
                DO $c7_binding_restore_loop$
                DECLARE
                    batch_count integer := 0;
                    total_count bigint := 0;
                BEGIN
                    LOOP
                        SELECT pg_temp.c7_restore_binding_batch(10000)
                        INTO batch_count;
                        total_count := total_count + batch_count;
                        RAISE NOTICE
                            'c7 restore bindings: batch %, total %',
                            batch_count,
                            total_count;
                        EXIT WHEN batch_count = 0;
                    END LOOP;
                END
                $c7_binding_restore_loop$
                """
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION pg_temp.c7_restore_binding_batch(integer)"
            )
        )
        return

    with context.autocommit_block():
        _create_binding_restore_batch_function()
    total = 0
    while True:
        with context.autocommit_block():
            batch_count = op.get_bind().scalar(
                sa.text(
                    "SELECT pg_temp.c7_restore_binding_batch(:batch_size)"
                ),
                {"batch_size": _BATCH_SIZE},
            )
        total += int(batch_count or 0)
        _LOGGER.info(
            "c7 restore bindings: batch %d, total %d",
            batch_count,
            total,
        )
        if not batch_count:
            break
    with context.autocommit_block():
        op.execute(
            sa.text(
                "DROP FUNCTION pg_temp.c7_restore_binding_batch(integer)"
            )
        )


def _add_not_valid_fk(
    *,
    name: str,
    table: str,
    local_columns: str,
    target: str,
    remote_columns: str,
    on_delete: str,
    deferrable: bool = False,
) -> None:
    deferred = " DEFERRABLE INITIALLY DEFERRED" if deferrable else ""
    op.execute(
        sa.text(
            f"""
            DO $c7_constraint$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = '{table}'::regclass
                      AND conname = '{name}'
                )
                THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name}
                    FOREIGN KEY({local_columns}) REFERENCES
                    {target} ({remote_columns}) ON DELETE {on_delete}
                    {deferred} NOT VALID;
                END IF;
            END
            $c7_constraint$
            """
        )
    )


def upgrade() -> None:
    # Expand only. The current repository may keep writing NULL compatibility
    # columns until candidate-aware writers land. No active-version default is
    # allowed because it would silently contaminate candidate builds.
    _add_column_if_missing(
        "knowledge_chunks",
        sa.Column("version_id", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "knowledge_entities",
        sa.Column("version_id", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "knowledge_relations",
        sa.Column("version_id", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "knowledge_entity_refs",
        sa.Column("version_id", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "knowledge_entities",
        sa.Column("normalized_name", sa.String(length=512), nullable=True),
    )
    _add_column_if_missing(
        "knowledge_bases",
        sa.Column("active_version_id", sa.String(length=255), nullable=True),
    )

    if _create_table_if_missing("knowledge_base_versions"):
        op.create_table(
        "knowledge_base_versions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=255), nullable=False),
        sa.Column("parent_version_id", sa.String(length=255), nullable=True),
        sa.Column("build_id", sa.String(length=255), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="building",
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "degraded_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "legacy_snapshot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('building', 'ready', 'degraded', 'failed')",
            name="ck_knowledge_base_versions_state",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_knowledge_base_versions_knowledge_base",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id", "knowledge_base_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_base_versions_parent_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["resource_builds.id"],
            name="fk_knowledge_base_versions_build",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "knowledge_base_id",
            name="uq_knowledge_base_versions_id_kb",
        ),
    )
    _create_index_if_missing(
        name="ix_knowledge_base_versions_kb_created",
        table="knowledge_base_versions",
        expression="(knowledge_base_id, created_at)",
    )
    _create_index_if_missing(
        name="ix_knowledge_base_versions_kb_published",
        table="knowledge_base_versions",
        expression="(knowledge_base_id, published_at)",
    )

    if _create_table_if_missing("knowledge_document_revisions"):
        op.create_table(
        "knowledge_document_revisions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=255), nullable=False),
        sa.Column(
            "source_ref",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "parsed_blocks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "page_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column(
            "needs_chunk_clone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "state IN "
            "('uploaded', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_document_revisions_state",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_document_revisions_document",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "document_id",
            name="uq_knowledge_document_revisions_id_document",
        ),
        sa.UniqueConstraint(
            "document_id",
            "source_digest",
            name="uq_knowledge_document_revisions_document_digest",
        ),
    )
    _create_index_if_missing(
        name="ix_knowledge_document_revisions_document_created",
        table="knowledge_document_revisions",
        expression="(document_id, created_at)",
    )

    # A composite manifest FK needs a unique logical-document ownership key.
    _create_concurrent_indexes()

    if _create_table_if_missing("knowledge_base_version_documents"):
        op.create_table(
        "knowledge_base_version_documents",
        sa.Column("version_id", sa.String(length=255), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=255), nullable=False),
        sa.Column("document_revision_id", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN "
            "('uploaded', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_base_version_documents_state",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "knowledge_base_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_kb_version_documents_version_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "knowledge_base_id"],
            ["knowledge_documents.id", "knowledge_documents.kb_id"],
            name="fk_kb_version_documents_document_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_revision_id", "document_id"],
            [
                "knowledge_document_revisions.id",
                "knowledge_document_revisions.document_id",
            ],
            name="fk_kb_version_documents_revision_document",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("version_id", "document_id"),
        sa.UniqueConstraint(
            "version_id",
            "document_id",
            name="uq_kb_version_documents_version_document",
        ),
        sa.UniqueConstraint(
            "version_id",
            "ordinal",
            name="uq_kb_version_documents_version_ordinal",
        ),
    )
    _create_index_if_missing(
        name="ix_kb_version_documents_revision",
        table="knowledge_base_version_documents",
        expression="(document_revision_id)",
    )

    _run_batched_sql(
        "knowledge versions",
        f"""
        WITH batch AS (
            SELECT kb.id
            FROM knowledge_bases AS kb
            WHERE NOT EXISTS (
                SELECT 1
                FROM knowledge_base_versions AS existing
                WHERE existing.id =
                    md5('knowledge-base-version:v1:' || kb.id)
            )
            ORDER BY kb.id
            LIMIT {_BATCH_SIZE}
            FOR UPDATE OF kb SKIP LOCKED
        )
        INSERT INTO knowledge_base_versions (
            id,
            knowledge_base_id,
            parent_version_id,
            build_id,
            state,
            capabilities,
            degraded_reasons,
            metrics,
            legacy_snapshot,
            created_at,
            published_at
        )
        SELECT
            md5('knowledge-base-version:v1:' || kb.id),
            kb.id,
            NULL,
            NULL,
            CASE
                WHEN kb.status = 'ready' AND NOT kb.vector_degraded
                    THEN 'ready'
                ELSE 'degraded'
            END,
            jsonb_build_object(
                'keyword_search', true,
                'vector_search', NOT kb.vector_degraded,
                'graph_search', true
            ),
            CASE
                WHEN kb.status <> 'ready' AND kb.vector_degraded
                    THEN '["LEGACY_STATE_UNVERIFIED",'
                         '"EMBEDDING_UNAVAILABLE"]'::jsonb
                WHEN kb.status <> 'ready'
                    THEN '["LEGACY_STATE_UNVERIFIED"]'::jsonb
                WHEN kb.vector_degraded
                    THEN '["EMBEDDING_UNAVAILABLE"]'::jsonb
                ELSE '[]'::jsonb
            END,
            '{{}}'::jsonb,
            true,
            kb.created_at,
            COALESCE(kb.updated_at, kb.created_at, CURRENT_TIMESTAMP)
        FROM batch
        JOIN knowledge_bases AS kb ON kb.id = batch.id
        ON CONFLICT (id) DO NOTHING
        """,
    )
    _run_batched_sql(
        "active versions",
        f"""
        WITH batch AS (
            SELECT kb.ctid
            FROM knowledge_bases AS kb
            WHERE kb.active_version_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM knowledge_base_versions AS version
                  WHERE version.id =
                      md5('knowledge-base-version:v1:' || kb.id)
              )
            LIMIT {_BATCH_SIZE}
            FOR UPDATE OF kb SKIP LOCKED
        )
        UPDATE knowledge_bases AS target
        SET active_version_id =
            md5('knowledge-base-version:v1:' || target.id)
        FROM batch
        WHERE target.ctid = batch.ctid
          AND target.active_version_id IS NULL
        """,
    )
    _run_batched_sql(
        "document revisions",
        f"""
        WITH batch AS (
            SELECT doc.id
            FROM knowledge_documents AS doc
            WHERE NOT EXISTS (
                SELECT 1
                FROM knowledge_document_revisions AS existing
                WHERE existing.id =
                    md5('knowledge-document-revision:v1:' || doc.id)
            )
            ORDER BY doc.id
            LIMIT {_BATCH_SIZE}
            FOR UPDATE OF doc SKIP LOCKED
        )
        INSERT INTO knowledge_document_revisions (
            id,
            document_id,
            source_ref,
            source_digest,
            parsed_blocks,
            page_count,
            state,
            needs_chunk_clone,
            error,
            warning,
            created_at
        )
        SELECT
            md5('knowledge-document-revision:v1:' || doc.id),
            doc.id,
            COALESCE(doc.source_ref, ''),
            md5(
                COALESCE(doc.source_ref, '') || ':' ||
                COALESCE(doc.file_id, '') || ':' ||
                doc.id
            ),
            '[]'::jsonb,
            COALESCE(doc.page_count, 0),
            CASE
                WHEN doc.status = 'ready' THEN 'indexed'
                WHEN doc.status = 'failed' THEN 'failed'
                ELSE 'parsed'
            END,
            (
                doc.status = 'ready'
                AND EXISTS (
                    SELECT 1
                    FROM knowledge_chunks AS legacy_chunk
                    WHERE legacy_chunk.kb_id = doc.kb_id
                      AND legacy_chunk.doc_id = doc.id
                      AND legacy_chunk.level = 'child'
                )
            ),
            doc.error,
            doc.warning,
            doc.created_at
        FROM batch
        JOIN knowledge_documents AS doc ON doc.id = batch.id
        ON CONFLICT (id) DO NOTHING
        """,
    )
    _run_batched_sql(
        "version manifests",
        f"""
        WITH batch AS (
            SELECT doc.id
            FROM knowledge_documents AS doc
            WHERE NOT EXISTS (
                SELECT 1
                FROM knowledge_base_version_documents AS existing
                WHERE existing.version_id =
                    md5('knowledge-base-version:v1:' || doc.kb_id)
                  AND existing.document_id = doc.id
            )
            ORDER BY doc.id
            LIMIT {_BATCH_SIZE}
            FOR UPDATE OF doc SKIP LOCKED
        )
        INSERT INTO knowledge_base_version_documents (
            version_id,
            knowledge_base_id,
            document_id,
            document_revision_id,
            ordinal,
            state,
            error,
            warning
        )
        SELECT
            md5('knowledge-base-version:v1:' || doc.kb_id),
            doc.kb_id,
            doc.id,
            md5('knowledge-document-revision:v1:' || doc.id),
            (
                SELECT count(*)::integer
                FROM knowledge_documents AS preceding
                WHERE preceding.kb_id = doc.kb_id
                  AND (
                      preceding.created_at < doc.created_at
                      OR (
                          preceding.created_at = doc.created_at
                          AND preceding.id < doc.id
                      )
                  )
            ),
            CASE
                WHEN doc.status = 'ready' THEN 'indexed'
                WHEN doc.status = 'failed' THEN 'failed'
                ELSE 'parsed'
            END,
            doc.error,
            doc.warning
        FROM batch
        JOIN knowledge_documents AS doc ON doc.id = batch.id
        ON CONFLICT (version_id, document_id) DO NOTHING
        """,
    )

    for table_name in (
        "knowledge_chunks",
        "knowledge_entities",
        "knowledge_relations",
        "knowledge_entity_refs",
    ):
        _run_batched_sql(
            table_name,
            f"""
            WITH batch AS (
                SELECT row.ctid
                FROM {table_name} AS row
                WHERE row.version_id IS NULL
                LIMIT {_BATCH_SIZE}
                FOR UPDATE OF row SKIP LOCKED
            )
            UPDATE {table_name} AS target
            SET version_id =
                md5('knowledge-base-version:v1:' || target.kb_id)
            FROM batch
            WHERE target.ctid = batch.ctid
              AND target.version_id IS NULL
            """,
        )

    _run_batched_sql(
        "normalized entities",
        f"""
        WITH batch AS (
            SELECT entity.ctid
            FROM knowledge_entities AS entity
            WHERE entity.normalized_name IS NULL
            LIMIT {_BATCH_SIZE}
            FOR UPDATE OF entity SKIP LOCKED
        )
        UPDATE knowledge_entities AS target
        SET normalized_name = CASE
            WHEN lower(btrim(target.name)) ~ '#legacy-[0-9a-f]{{32}}$'
              OR EXISTS (
                SELECT 1
                FROM knowledge_entities AS earlier
                WHERE earlier.version_id IS NOT DISTINCT FROM target.version_id
                  AND lower(btrim(earlier.name)) =
                      lower(btrim(target.name))
                  AND earlier.type = target.type
                  AND earlier.id < target.id
            )
                THEN left(lower(btrim(target.name)), 470) ||
                     '#legacy-' ||
                     md5(target.id)
            ELSE lower(btrim(target.name))
        END
        FROM batch
        WHERE target.ctid = batch.ctid
          AND target.normalized_name IS NULL
        """,
    )
    _backfill_bindings()

    # Composite constraints are intentionally NOT VALID in expand c7. They
    # enforce every new explicit-version row immediately while avoiding a
    # blocking validation scan. A later contract migration validates them and
    # sets NOT NULL only after CONTRACT_MIGRATION_GATE is satisfied.
    _add_not_valid_fk(
        name="fk_knowledge_bases_active_version_owner",
        table="knowledge_bases",
        local_columns="active_version_id, id",
        target="knowledge_base_versions",
        remote_columns="id, knowledge_base_id",
        on_delete="NO ACTION",
        deferrable=True,
    )
    _add_not_valid_fk(
        name="fk_knowledge_chunks_version_owner",
        table="knowledge_chunks",
        local_columns="version_id, kb_id",
        target="knowledge_base_versions",
        remote_columns="id, knowledge_base_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_chunks_manifest_membership",
        table="knowledge_chunks",
        local_columns="version_id, doc_id",
        target="knowledge_base_version_documents",
        remote_columns="version_id, document_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_entities_version_owner",
        table="knowledge_entities",
        local_columns="version_id, kb_id",
        target="knowledge_base_versions",
        remote_columns="id, knowledge_base_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_relations_version_owner",
        table="knowledge_relations",
        local_columns="version_id, kb_id",
        target="knowledge_base_versions",
        remote_columns="id, knowledge_base_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_relations_version_src",
        table="knowledge_relations",
        local_columns="version_id, src_entity_id",
        target="knowledge_entities",
        remote_columns="version_id, id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_relations_version_dst",
        table="knowledge_relations",
        local_columns="version_id, dst_entity_id",
        target="knowledge_entities",
        remote_columns="version_id, id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_relations_version_chunk",
        table="knowledge_relations",
        local_columns="version_id, chunk_id",
        target="knowledge_chunks",
        remote_columns="version_id, id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_entity_refs_version_owner",
        table="knowledge_entity_refs",
        local_columns="version_id, kb_id",
        target="knowledge_base_versions",
        remote_columns="id, knowledge_base_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_entity_refs_manifest_membership",
        table="knowledge_entity_refs",
        local_columns="version_id, doc_id",
        target="knowledge_base_version_documents",
        remote_columns="version_id, document_id",
        on_delete="CASCADE",
    )
    _add_not_valid_fk(
        name="fk_knowledge_entity_refs_version_entity",
        table="knowledge_entity_refs",
        local_columns="version_id, entity_id",
        target="knowledge_entities",
        remote_columns="version_id, id",
        on_delete="CASCADE",
    )


def downgrade() -> None:
    _restore_legacy_bindings()

    for table_name, constraint_name in (
        (
            "knowledge_entity_refs",
            "fk_knowledge_entity_refs_version_entity",
        ),
        (
            "knowledge_entity_refs",
            "fk_knowledge_entity_refs_manifest_membership",
        ),
        (
            "knowledge_entity_refs",
            "fk_knowledge_entity_refs_version_owner",
        ),
        (
            "knowledge_relations",
            "fk_knowledge_relations_version_chunk",
        ),
        ("knowledge_relations", "fk_knowledge_relations_version_dst"),
        ("knowledge_relations", "fk_knowledge_relations_version_src"),
        ("knowledge_relations", "fk_knowledge_relations_version_owner"),
        ("knowledge_entities", "fk_knowledge_entities_version_owner"),
        (
            "knowledge_chunks",
            "fk_knowledge_chunks_manifest_membership",
        ),
        ("knowledge_chunks", "fk_knowledge_chunks_version_owner"),
        (
            "knowledge_bases",
            "fk_knowledge_bases_active_version_owner",
        ),
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} "
                f"DROP CONSTRAINT IF EXISTS {constraint_name}"
            )
        )

    op.execute(
        sa.text(
            "ALTER TABLE knowledge_bases "
            "DROP COLUMN IF EXISTS active_version_id"
        )
    )

    # The manifest's document-owner FK depends on the concurrently-created
    # unique document ownership index. Drop the dependent table before that
    # index, then remove large-table indexes before their columns.
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS ix_kb_version_documents_revision"
        )
    )
    op.execute(
        sa.text("DROP TABLE IF EXISTS knowledge_base_version_documents")
    )
    _drop_concurrent_indexes()

    for table_name in (
        "knowledge_entity_refs",
        "knowledge_relations",
        "knowledge_entities",
        "knowledge_chunks",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} "
                "DROP COLUMN IF EXISTS version_id"
            )
        )
    op.execute(
        sa.text(
            "ALTER TABLE knowledge_entities "
            "DROP COLUMN IF EXISTS normalized_name"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS "
            "ix_knowledge_document_revisions_document_created"
        )
    )
    op.execute(
        sa.text("DROP TABLE IF EXISTS knowledge_document_revisions")
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS ix_knowledge_base_versions_kb_published"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS ix_knowledge_base_versions_kb_created"
        )
    )
    op.execute(
        sa.text("DROP TABLE IF EXISTS knowledge_base_versions")
    )
