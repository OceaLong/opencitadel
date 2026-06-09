"""create marketplace fortune predictions table

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, Sequence[str], None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_fortune_predictions",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("share_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("input_profile", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index(
        "ix_fortune_predictions_share_id",
        "marketplace_fortune_predictions",
        ["share_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_fortune_predictions_share_id", table_name="marketplace_fortune_predictions")
    op.drop_table("marketplace_fortune_predictions")
