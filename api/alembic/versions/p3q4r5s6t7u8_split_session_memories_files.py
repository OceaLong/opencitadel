"""split sessions memories/files JSONB into dedicated tables

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p3q4r5s6t7u8"
down_revision: Union[str, None] = "o2p3q4r5s6t7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_agent_memories",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column(
            "memory_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{\"messages\": []}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "agent_name", name="pk_session_agent_memories"),
    )
    op.create_index(
        "ix_session_agent_memories_session_id",
        "session_agent_memories",
        ["session_id"],
    )

    op.create_table(
        "session_file_attachments",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("filepath", sa.String(length=1024), nullable=False, server_default=sa.text("''")),
        sa.Column("key", sa.String(length=1024), nullable=False, server_default=sa.text("''")),
        sa.Column("extension", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("mime_type", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "file_id", name="pk_session_file_attachments"),
    )
    op.create_index(
        "ix_session_file_attachments_session_id",
        "session_file_attachments",
        ["session_id"],
    )
    op.create_index(
        "ix_session_file_attachments_session_filepath",
        "session_file_attachments",
        ["session_id", "filepath"],
    )

    # Migrate existing JSONB data
    op.execute(
        """
        INSERT INTO session_agent_memories (session_id, agent_name, memory_data)
        SELECT s.id, kv.key, kv.value
        FROM sessions s
        CROSS JOIN LATERAL jsonb_each(COALESCE(s.memories, '{}'::jsonb)) AS kv(key, value)
        ON CONFLICT (session_id, agent_name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO session_file_attachments (
            session_id, file_id, filename, filepath, key, extension, mime_type, size
        )
        SELECT
            s.id,
            COALESCE(f.elem->>'id', gen_random_uuid()::text),
            COALESCE(f.elem->>'filename', ''),
            COALESCE(f.elem->>'filepath', ''),
            COALESCE(f.elem->>'key', ''),
            COALESCE(f.elem->>'extension', ''),
            COALESCE(f.elem->>'mime_type', ''),
            COALESCE((f.elem->>'size')::int, 0)
        FROM sessions s
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.files, '[]'::jsonb)) AS f(elem)
        ON CONFLICT (session_id, file_id) DO NOTHING
        """
    )

    op.drop_column("sessions", "memories")
    op.drop_column("sessions", "files")


def downgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "memories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "files",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.execute(
        """
        UPDATE sessions s
        SET memories = COALESCE(
            (
                SELECT jsonb_object_agg(m.agent_name, m.memory_data)
                FROM session_agent_memories m
                WHERE m.session_id = s.id
            ),
            '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        UPDATE sessions s
        SET files = COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', f.file_id,
                        'filename', f.filename,
                        'filepath', f.filepath,
                        'key', f.key,
                        'extension', f.extension,
                        'mime_type', f.mime_type,
                        'size', f.size
                    )
                )
                FROM session_file_attachments f
                WHERE f.session_id = s.id
            ),
            '[]'::jsonb
        )
        """
    )

    op.drop_index("ix_session_file_attachments_session_filepath", table_name="session_file_attachments")
    op.drop_index("ix_session_file_attachments_session_id", table_name="session_file_attachments")
    op.drop_table("session_file_attachments")
    op.drop_index("ix_session_agent_memories_session_id", table_name="session_agent_memories")
    op.drop_table("session_agent_memories")
