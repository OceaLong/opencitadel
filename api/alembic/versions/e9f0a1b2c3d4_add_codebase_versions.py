"""Add immutable codebase analysis versions.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONCURRENT_INDEXES: tuple[str, ...] = (
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_files_codebase_path",
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_files_codebase_path "
        "ON codebase_files (codebase_id, path)"
    ),
    (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_codebase_files_version_path "
        "ON codebase_files (version_id, path)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_files_version "
        "ON codebase_files (version_id)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_symbols_version_qualified_name "
        "ON codebase_symbols (version_id, qualified_name)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_symbols_version_file "
        "ON codebase_symbols (version_id, file_id)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_edges_version_src "
        "ON codebase_edges (version_id, src_symbol_id)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_edges_version_dst "
        "ON codebase_edges (version_id, dst_symbol_id)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_chunks_version "
        "ON codebase_chunks (version_id)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_chunks_search_vector "
        "ON codebase_chunks USING GIN (search_vector)"
    ),
    (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_artifacts_version_kind "
        "ON codebase_artifacts (version_id, kind)"
    ),
)

_DOWN_CONCURRENT_INDEXES: tuple[str, ...] = (
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_artifacts_version_kind",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_chunks_search_vector",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_chunks_version",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_edges_version_dst",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_edges_version_src",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_symbols_version_file",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_symbols_version_qualified_name",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_files_version",
    "DROP INDEX CONCURRENTLY IF EXISTS uq_codebase_files_version_path",
    "DROP INDEX CONCURRENTLY IF EXISTS ix_codebase_files_codebase_path",
    (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_codebase_files_codebase_path "
        "ON codebase_files (codebase_id, path)"
    ),
)


def _execute_concurrently(statements: tuple[str, ...]) -> None:
    context = op.get_context()
    for statement in statements:
        with context.autocommit_block():
            op.execute(sa.text(statement))


def upgrade() -> None:
    op.create_table(
        "codebase_versions",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), nullable=False),
        sa.Column("parent_version_id", sa.String(255), nullable=True),
        sa.Column("build_id", sa.String(255), nullable=True),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'building'"),
        ),
        sa.Column("source_snapshot_key", sa.Text(), nullable=True),
        sa.Column(
            "source_revision",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "source_digest",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
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
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["codebase_id"],
            ["codebases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["resource_builds.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id", "codebase_id"],
            ["codebase_versions.id", "codebase_versions.codebase_id"],
            name="fk_codebase_versions_parent_owner",
            ondelete="SET NULL (parent_version_id)",
        ),
        sa.UniqueConstraint(
            "id",
            "codebase_id",
            name="uq_codebase_versions_id_owner",
        ),
    )
    op.create_index(
        "ix_codebase_versions_codebase_state",
        "codebase_versions",
        ["codebase_id", "state"],
    )
    op.create_index(
        "ix_codebase_versions_build",
        "codebase_versions",
        ["build_id"],
    )

    op.add_column(
        "codebases",
        sa.Column("active_version_id", sa.String(255), nullable=True),
    )
    for table in (
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
    ):
        op.add_column(
            table,
            sa.Column("version_id", sa.String(255), nullable=True),
        )

    op.add_column(
        "codebase_symbols",
        sa.Column(
            "qualified_name",
            sa.String(1024),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "codebase_symbols",
        sa.Column(
            "parser",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'regex'"),
        ),
    )
    op.add_column(
        "codebase_symbols",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "codebase_edges",
        sa.Column(
            "resolution",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'unresolved'"),
        ),
    )
    op.add_column(
        "codebase_edges",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "codebase_edges",
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "codebase_chunks",
        sa.Column(
            "search_text",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE codebase_chunks
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(search_text, ''))
            ) STORED
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO codebase_versions (
                id,
                codebase_id,
                state,
                source_snapshot_key,
                source_revision,
                source_digest,
                capabilities,
                degraded_reasons,
                metrics,
                legacy_snapshot,
                created_at,
                published_at
            )
            SELECT
                md5('codebase-version:v1:' || cb.id),
                cb.id,
                CASE
                    WHEN cb.status = 'ready' AND cb.vector_degraded
                        THEN 'degraded'
                    WHEN cb.status = 'ready'
                        THEN 'ready'
                    ELSE 'failed'
                END,
                cb.snapshot_key,
                'legacy-v1',
                md5(coalesce(cb.snapshot_key, '') || ':' || cb.id),
                jsonb_build_object(
                    'lexical_search', cb.status = 'ready',
                    'source_read', cb.snapshot_key IS NOT NULL,
                    'vector_search', NOT cb.vector_degraded,
                    'artifact_generation', cb.status = 'ready'
                ),
                CASE
                    WHEN cb.vector_degraded
                        THEN '["EMBEDDING_UNAVAILABLE"]'::jsonb
                    ELSE '[]'::jsonb
                END,
                jsonb_build_object('file_count', cb.file_count),
                true,
                cb.created_at,
                CASE
                    WHEN cb.status = 'ready'
                        THEN coalesce(cb.updated_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END
            FROM codebases AS cb
            WHERE NOT EXISTS (
                SELECT 1
                FROM codebase_versions AS existing
                WHERE existing.id = md5('codebase-version:v1:' || cb.id)
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE codebases AS target
            SET active_version_id = md5('codebase-version:v1:' || target.id),
                legacy_v1_migrated = true
            WHERE target.active_version_id IS NULL
              AND EXISTS (
                    SELECT 1
                    FROM codebase_versions AS version
                    WHERE version.id = md5('codebase-version:v1:' || target.id)
                      AND version.published_at IS NOT NULL
              )
            """
        )
    )

    for table in (
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS target
                SET version_id =
                    md5('codebase-version:v1:' || target.codebase_id)
                WHERE target.version_id IS NULL
                  AND EXISTS (
                        SELECT 1
                        FROM codebase_versions AS version
                        WHERE version.id =
                            md5('codebase-version:v1:' || target.codebase_id)
                  )
                """
            )
        )

    op.execute(
        sa.text(
            """
            UPDATE codebase_symbols AS target
            SET qualified_name = target.name
            WHERE target.qualified_name = ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE codebase_chunks AS target
            SET search_text = coalesce(target.content, '')
            WHERE target.search_text = ''
            """
        )
    )

    op.execute(
        sa.text(
            """
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
            SELECT
                md5(
                    'session-codebase-binding:e9:' ||
                    session_row.id || ':' || session_row.codebase_id
                ),
                session_row.id,
                'codebase',
                session_row.codebase_id,
                md5('codebase-version:v1:' || session_row.codebase_id),
                true,
                NULL,
                'migration:e9f0a1b2c3d4',
                CURRENT_TIMESTAMP
            FROM sessions AS session_row
            WHERE session_row.codebase_id IS NOT NULL
              AND EXISTS (
                    SELECT 1
                    FROM codebase_versions AS version
                    WHERE version.id =
                        md5(
                            'codebase-version:v1:' ||
                            session_row.codebase_id
                        )
                      AND version.published_at IS NOT NULL
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM session_resource_bindings AS binding
                    WHERE binding.session_id = session_row.id
                      AND binding.resource_kind = 'codebase'
                      AND binding.is_current = true
              )
            """
        )
    )

    op.create_foreign_key(
        "fk_codebases_active_version_owner",
        "codebases",
        "codebase_versions",
        ["active_version_id", "id"],
        ["id", "codebase_id"],
        ondelete="SET NULL",
    )
    for table in (
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
    ):
        op.create_foreign_key(
            f"fk_{table}_version",
            table,
            "codebase_versions",
            ["version_id"],
            ["id"],
            ondelete="CASCADE",
        )

    _execute_concurrently(_CONCURRENT_INDEXES)


def downgrade() -> None:
    _execute_concurrently(_DOWN_CONCURRENT_INDEXES)

    op.drop_constraint(
        "fk_codebases_active_version_owner",
        "codebases",
        type_="foreignkey",
    )
    for table in (
        "codebase_artifacts",
        "codebase_chunks",
        "codebase_edges",
        "codebase_symbols",
        "codebase_files",
    ):
        op.drop_constraint(
            f"fk_{table}_version",
            table,
            type_="foreignkey",
        )

    op.execute(
        sa.text(
            """
            DELETE FROM session_resource_bindings AS marker
            WHERE marker.bound_by = 'migration:e9f0a1b2c3d4'
              AND marker.resource_kind = 'codebase'
            """
        )
    )

    op.drop_column("codebase_artifacts", "version_id")
    op.execute(sa.text("ALTER TABLE codebase_chunks DROP COLUMN search_vector"))
    op.drop_column("codebase_chunks", "search_text")
    op.drop_column("codebase_chunks", "version_id")
    op.drop_column("codebase_edges", "evidence")
    op.drop_column("codebase_edges", "confidence")
    op.drop_column("codebase_edges", "resolution")
    op.drop_column("codebase_edges", "version_id")
    op.drop_column("codebase_symbols", "confidence")
    op.drop_column("codebase_symbols", "parser")
    op.drop_column("codebase_symbols", "qualified_name")
    op.drop_column("codebase_symbols", "version_id")
    op.drop_column("codebase_files", "version_id")
    op.drop_column("codebases", "active_version_id")

    op.drop_index(
        "ix_codebase_versions_build",
        table_name="codebase_versions",
    )
    op.drop_index(
        "ix_codebase_versions_codebase_state",
        table_name="codebase_versions",
    )
    op.drop_table("codebase_versions")
