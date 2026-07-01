"""create knowledge bases tables

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "t8u9v0w1x2y3"
down_revision: Union[str, Sequence[str], None] = "s7t8u9v0w1x2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ingest_task_id", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("vector_degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("settings", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(255),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default=sa.text("'upload'")),
        sa.Column("source_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("mime", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("file_id", sa.String(255), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_kb_documents_kb_status", "knowledge_documents", ["kb_id", "status"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(255),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_id",
            sa.String(255),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.String(255), nullable=True),
        sa.Column("level", sa.String(16), nullable=False, server_default=sa.text("'child'")),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("heading_path", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ordinal", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_parent_id",
        "knowledge_chunks",
        "knowledge_chunks",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(1536)")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN content_tsv tsvector")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_kb_chunks_tsv ON knowledge_chunks USING gin (content_tsv)")
    op.create_index("ix_kb_chunks_kb_level", "knowledge_chunks", ["kb_id", "level"])
    op.create_index("ix_kb_chunks_parent", "knowledge_chunks", ["parent_id"])
    op.create_index("ix_kb_chunks_doc_ordinal", "knowledge_chunks", ["doc_id", "ordinal"])

    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(255),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("type", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.create_index("ix_kb_entities_name", "knowledge_entities", ["kb_id", "name"])

    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(255),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "src_entity_id",
            sa.String(255),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_entity_id",
            sa.String(255),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "chunk_id",
            sa.String(255),
            sa.ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_kb_relations_src", "knowledge_relations", ["kb_id", "src_entity_id"])
    op.create_index("ix_kb_relations_dst", "knowledge_relations", ["kb_id", "dst_entity_id"])

    op.add_column("sessions", sa.Column("knowledge_base_id", sa.String(255), nullable=True))
    op.create_foreign_key(
        "fk_sessions_knowledge_base_id",
        "sessions",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_knowledge_base_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "knowledge_base_id")
    op.drop_index("ix_kb_relations_dst", table_name="knowledge_relations")
    op.drop_index("ix_kb_relations_src", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")
    op.drop_index("ix_kb_entities_name", table_name="knowledge_entities")
    op.drop_table("knowledge_entities")
    op.drop_index("ix_kb_chunks_doc_ordinal", table_name="knowledge_chunks")
    op.drop_index("ix_kb_chunks_parent", table_name="knowledge_chunks")
    op.drop_index("ix_kb_chunks_kb_level", table_name="knowledge_chunks")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_tsv")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding")
    op.drop_constraint("fk_knowledge_chunks_parent_id", "knowledge_chunks", type_="foreignkey")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_kb_documents_kb_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
