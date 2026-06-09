"""create codebases tables and extend sessions

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, Sequence[str], None] = "h8i9j0k1l2m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "codebases",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("source_type", sa.String(32), nullable=False, server_default=sa.text("'files'")),
        sa.Column("source_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("language_stats", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sandbox_id", sa.String(255), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=False, server_default=sa.text("'/home/ubuntu/codebase'")),
        sa.Column("snapshot_key", sa.Text(), nullable=True),
        sa.Column("ingest_task_id", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )

    op.create_table(
        "codebase_files",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), sa.ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("language", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sha", sa.String(64), nullable=False, server_default=sa.text("''")),
    )
    op.create_index("ix_codebase_files_codebase_path", "codebase_files", ["codebase_id", "path"], unique=True)

    op.create_table(
        "codebase_symbols",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), sa.ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(255), sa.ForeignKey("codebase_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default=sa.text("'function'")),
        sa.Column("signature", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("start_line", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("end_line", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("parent_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_codebase_symbols_codebase_name", "codebase_symbols", ["codebase_id", "name"])

    op.create_table(
        "codebase_edges",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), sa.ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("src_symbol_id", sa.String(255), sa.ForeignKey("codebase_symbols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dst_symbol_id", sa.String(255), sa.ForeignKey("codebase_symbols.id", ondelete="SET NULL"), nullable=True),
        sa.Column("callee_name", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("kind", sa.String(32), nullable=False, server_default=sa.text("'call'")),
    )
    op.create_index("ix_codebase_edges_src", "codebase_edges", ["codebase_id", "src_symbol_id"])

    op.create_table(
        "codebase_chunks",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), sa.ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(255), sa.ForeignKey("codebase_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol_id", sa.String(255), sa.ForeignKey("codebase_symbols.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.execute("ALTER TABLE codebase_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("ix_codebase_chunks_codebase", "codebase_chunks", ["codebase_id"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_codebase_chunks_embedding "
        "ON codebase_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "codebase_artifacts",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("codebase_id", sa.String(255), sa.ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default=sa.text("'mermaid'")),
        sa.Column("title", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_codebase_artifacts_codebase_kind", "codebase_artifacts", ["codebase_id", "kind"])

    op.add_column("sessions", sa.Column("codebase_id", sa.String(255), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("mode", sa.String(16), nullable=False, server_default=sa.text("'agent'")),
    )
    op.create_foreign_key(
        "fk_sessions_codebase_id",
        "sessions",
        "codebases",
        ["codebase_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_codebase_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "mode")
    op.drop_column("sessions", "codebase_id")
    op.drop_index("ix_codebase_artifacts_codebase_kind", table_name="codebase_artifacts")
    op.drop_table("codebase_artifacts")
    op.execute("DROP INDEX IF EXISTS ix_codebase_chunks_embedding")
    op.drop_index("ix_codebase_chunks_codebase", table_name="codebase_chunks")
    op.drop_table("codebase_chunks")
    op.drop_index("ix_codebase_edges_src", table_name="codebase_edges")
    op.drop_table("codebase_edges")
    op.drop_index("ix_codebase_symbols_codebase_name", table_name="codebase_symbols")
    op.drop_table("codebase_symbols")
    op.drop_index("ix_codebase_files_codebase_path", table_name="codebase_files")
    op.drop_table("codebase_files")
    op.drop_table("codebases")
