"""Remove compatibility state after immutable-resource contract migration.

Revision ID: aa06contract
Revises: 94feff5b0d54
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aa06contract"
down_revision: Union[str, None] = "94feff5b0d54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _backfill_session_bindings() -> None:
    _execute(
        """
        INSERT INTO session_resource_bindings (
            id, session_id, resource_kind, resource_id, version_id,
            is_current, supersedes_binding_id, bound_by, created_at
        )
        SELECT
            md5('aa06:codebase:' || session_row.id),
            session_row.id,
            'codebase',
            session_row.codebase_id,
            codebase.active_version_id,
            true,
            NULL,
            'migration:aa06contract',
            CURRENT_TIMESTAMP
        FROM sessions AS session_row
        JOIN codebases AS codebase
          ON codebase.id = session_row.codebase_id
        JOIN codebase_versions AS version
          ON version.id = codebase.active_version_id
         AND version.codebase_id = codebase.id
         AND version.published_at IS NOT NULL
         AND version.state IN ('ready', 'degraded')
        WHERE session_row.codebase_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM session_resource_bindings AS binding
              WHERE binding.session_id = session_row.id
                AND binding.resource_kind = 'codebase'
                AND binding.is_current IS TRUE
          )
        ON CONFLICT (id) DO NOTHING
        """
    )
    _execute(
        """
        INSERT INTO session_resource_bindings (
            id, session_id, resource_kind, resource_id, version_id,
            is_current, supersedes_binding_id, bound_by, created_at
        )
        SELECT
            md5('aa06:knowledge-base:' || session_row.id),
            session_row.id,
            'knowledge_base',
            session_row.knowledge_base_id,
            knowledge_base.active_version_id,
            true,
            NULL,
            'migration:aa06contract',
            CURRENT_TIMESTAMP
        FROM sessions AS session_row
        JOIN knowledge_bases AS knowledge_base
          ON knowledge_base.id = session_row.knowledge_base_id
        JOIN knowledge_base_versions AS version
          ON version.id = knowledge_base.active_version_id
         AND version.knowledge_base_id = knowledge_base.id
         AND version.published_at IS NOT NULL
         AND version.state IN ('ready', 'degraded')
        WHERE session_row.knowledge_base_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM session_resource_bindings AS binding
              WHERE binding.session_id = session_row.id
                AND binding.resource_kind = 'knowledge_base'
                AND binding.is_current IS TRUE
          )
        ON CONFLICT (id) DO NOTHING
        """
    )
    _execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM sessions AS session_row
                WHERE session_row.codebase_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM session_resource_bindings AS binding
                      JOIN codebase_versions AS version
                        ON version.id = binding.version_id
                       AND version.codebase_id = binding.resource_id
                       AND version.published_at IS NOT NULL
                       AND version.state IN ('ready', 'degraded')
                      WHERE binding.session_id = session_row.id
                        AND binding.resource_kind = 'codebase'
                        AND binding.resource_id = session_row.codebase_id
                        AND binding.is_current IS TRUE
                  )
            ) THEN
                RAISE EXCEPTION 'unresolved codebase session bindings';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM sessions AS session_row
                WHERE session_row.knowledge_base_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM session_resource_bindings AS binding
                      JOIN knowledge_base_versions AS version
                        ON version.id = binding.version_id
                       AND version.knowledge_base_id = binding.resource_id
                       AND version.published_at IS NOT NULL
                       AND version.state IN ('ready', 'degraded')
                      WHERE binding.session_id = session_row.id
                        AND binding.resource_kind = 'knowledge_base'
                        AND binding.resource_id = session_row.knowledge_base_id
                        AND binding.is_current IS TRUE
                  )
            ) THEN
                RAISE EXCEPTION 'unresolved knowledge-base session bindings';
            END IF;
        END
        $$
        """
    )


def _contract_knowledge_rows() -> None:
    _execute(
        """
        UPDATE knowledge_chunks AS target
        SET version_id = knowledge_base.active_version_id
        FROM knowledge_bases AS knowledge_base
        WHERE target.version_id IS NULL
          AND knowledge_base.id = target.kb_id
          AND knowledge_base.active_version_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM knowledge_base_version_documents AS manifest
              WHERE manifest.version_id = knowledge_base.active_version_id
                AND manifest.knowledge_base_id = target.kb_id
                AND manifest.document_id = target.doc_id
          )
        """
    )
    _execute(
        """
        UPDATE knowledge_entities AS target
        SET version_id = knowledge_base.active_version_id
        FROM knowledge_bases AS knowledge_base
        WHERE target.version_id IS NULL
          AND knowledge_base.id = target.kb_id
          AND knowledge_base.active_version_id IS NOT NULL
        """
    )
    _execute(
        """
        UPDATE knowledge_relations AS target
        SET version_id = knowledge_base.active_version_id
        FROM knowledge_bases AS knowledge_base,
             knowledge_entities AS source,
             knowledge_entities AS destination
        WHERE target.version_id IS NULL
          AND knowledge_base.id = target.kb_id
          AND source.id = target.src_entity_id
          AND destination.id = target.dst_entity_id
          AND source.version_id = knowledge_base.active_version_id
          AND destination.version_id = knowledge_base.active_version_id
          AND (
              target.chunk_id IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM knowledge_chunks AS chunk
                  WHERE chunk.id = target.chunk_id
                    AND chunk.version_id = knowledge_base.active_version_id
              )
          )
        """
    )
    _execute(
        """
        UPDATE knowledge_entity_refs AS target
        SET version_id = knowledge_base.active_version_id
        FROM knowledge_bases AS knowledge_base,
             knowledge_entities AS entity
        WHERE target.version_id IS NULL
          AND knowledge_base.id = target.kb_id
          AND entity.id = target.entity_id
          AND entity.version_id = knowledge_base.active_version_id
          AND EXISTS (
              SELECT 1
              FROM knowledge_base_version_documents AS manifest
              WHERE manifest.version_id = knowledge_base.active_version_id
                AND manifest.knowledge_base_id = target.kb_id
                AND manifest.document_id = target.doc_id
          )
        """
    )
    _execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM knowledge_chunks WHERE version_id IS NULL
                UNION ALL
                SELECT 1 FROM knowledge_entities WHERE version_id IS NULL
                UNION ALL
                SELECT 1 FROM knowledge_relations WHERE version_id IS NULL
                UNION ALL
                SELECT 1 FROM knowledge_entity_refs WHERE version_id IS NULL
            ) THEN
                RAISE EXCEPTION 'unversioned knowledge rows remain';
            END IF;
        END
        $$
        """
    )

    for table_name, constraint_name in (
        ("knowledge_chunks", "fk_knowledge_chunks_version_owner"),
        ("knowledge_chunks", "fk_knowledge_chunks_manifest_membership"),
        ("knowledge_entities", "fk_knowledge_entities_version_owner"),
        ("knowledge_relations", "fk_knowledge_relations_version_owner"),
        ("knowledge_relations", "fk_knowledge_relations_version_src"),
        ("knowledge_relations", "fk_knowledge_relations_version_dst"),
        ("knowledge_relations", "fk_knowledge_relations_version_chunk"),
        ("knowledge_entity_refs", "fk_knowledge_entity_refs_version_owner"),
        (
            "knowledge_entity_refs",
            "fk_knowledge_entity_refs_manifest_membership",
        ),
        (
            "knowledge_entity_refs",
            "fk_knowledge_entity_refs_version_entity",
        ),
    ):
        _execute(
            f"ALTER TABLE {table_name} "
            f"VALIDATE CONSTRAINT {constraint_name}"
        )

    for table_name in (
        "knowledge_chunks",
        "knowledge_entities",
        "knowledge_relations",
        "knowledge_entity_refs",
    ):
        op.alter_column(
            table_name,
            "version_id",
            existing_type=sa.String(length=255),
            nullable=False,
        )


def upgrade() -> None:
    _backfill_session_bindings()
    _contract_knowledge_rows()

    _execute(
        """
        UPDATE sessions
        SET pending_phase = NULL,
            pending_metadata = NULLIF(
                pending_metadata - 'pending_tool_call' - 'awaiting_human',
                '{}'::jsonb
            )
        WHERE pending_phase = 'tool_approval'
          AND COALESCE(pending_metadata ->> 'approval_batch_id', '') = ''
        """
    )
    _execute(
        """
        UPDATE sessions
        SET pending_metadata = NULLIF(
            pending_metadata - 'pending_tool_call',
            '{}'::jsonb
        )
        WHERE pending_metadata ? 'pending_tool_call'
        """
    )

    op.drop_constraint(
        "fk_sessions_codebase_id",
        "sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_sessions_knowledge_base_id",
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "codebase_id")
    op.drop_column("sessions", "knowledge_base_id")

    op.drop_column("codebases", "legacy_v1_migrated")
    op.drop_column("knowledge_bases", "legacy_v1_migrated")
    op.drop_column("codebase_versions", "legacy_snapshot")
    op.drop_column("knowledge_base_versions", "legacy_snapshot")


def downgrade() -> None:
    op.add_column(
        "codebases",
        sa.Column(
            "legacy_v1_migrated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "legacy_v1_migrated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "codebase_versions",
        sa.Column(
            "legacy_snapshot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "knowledge_base_versions",
        sa.Column(
            "legacy_snapshot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    for table_name in (
        "knowledge_entity_refs",
        "knowledge_relations",
        "knowledge_entities",
        "knowledge_chunks",
    ):
        op.alter_column(
            table_name,
            "version_id",
            existing_type=sa.String(length=255),
            nullable=True,
        )

    op.add_column(
        "sessions",
        sa.Column("codebase_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "knowledge_base_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_sessions_codebase_id",
        "sessions",
        "codebases",
        ["codebase_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sessions_knowledge_base_id",
        "sessions",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _execute(
        """
        UPDATE sessions AS session_row
        SET codebase_id = binding.resource_id
        FROM session_resource_bindings AS binding
        WHERE binding.session_id = session_row.id
          AND binding.resource_kind = 'codebase'
          AND binding.is_current IS TRUE
        """
    )
    _execute(
        """
        UPDATE sessions AS session_row
        SET knowledge_base_id = binding.resource_id
        FROM session_resource_bindings AS binding
        WHERE binding.session_id = session_row.id
          AND binding.resource_kind = 'knowledge_base'
          AND binding.is_current IS TRUE
        """
    )
