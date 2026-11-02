"""Freeze KB vector spaces and isolate memory vectors by space.

The initial migration creates live Base metadata. Validate columns already
created on a fresh database; add them only when advancing an existing schema.
Old vectors deliberately retain NULL identity and cannot enter vector queries.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0031phase1_embedding_space"
down_revision = "0030evaluation_judge_history"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    expected = {
        "knowledge_base_versions": [
            sa.Column("embedding_space", JSONB(), nullable=True),
            sa.Column("embedding_space_digest", sa.String(64), nullable=True),
        ],
        "memory_entries": [sa.Column("embedding_space_digest", sa.String(64), nullable=True)],
    }
    for table, columns in expected.items():
        actual = {column["name"]: column for column in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in actual:
                op.add_column(table, column)
                continue
            found = actual[column.name]
            correct_type = (
                isinstance(found["type"], JSONB)
                if isinstance(column.type, JSONB)
                else isinstance(found["type"], sa.String) and found["type"].length == 64
            )
            if not correct_type or not found["nullable"]:
                raise RuntimeError(
                    f"unexpected embedding-space column shape: {table}.{column.name}"
                )
    indexes = {item["name"]: item for item in sa.inspect(bind).get_indexes("memory_entries")}
    if "ix_memory_entries_embedding_space" not in indexes:
        op.create_index(
            "ix_memory_entries_embedding_space", "memory_entries", ["embedding_space_digest"]
        )
    elif indexes["ix_memory_entries_embedding_space"]["column_names"] != ["embedding_space_digest"]:
        raise RuntimeError("unexpected embedding-space index shape")


def downgrade():
    raise NotImplementedError(
        "embedding-space identity cannot be discarded; rebuild a development database"
    )
