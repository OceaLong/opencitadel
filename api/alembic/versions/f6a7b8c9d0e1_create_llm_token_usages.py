"""create llm_token_usages and model pricing columns

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_token_usages",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("session_id", sa.String(255), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent", sa.String(128), nullable=False, server_default=""),
        sa.Column("step", sa.String(255), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(255), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("call_type", sa.String(32), nullable=False, server_default="stream"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_llm_token_usages_session_id", "llm_token_usages", ["session_id"])
    op.add_column(
        "llm_models",
        sa.Column("input_price_per_million", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "llm_models",
        sa.Column("output_price_per_million", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("llm_models", "output_price_per_million")
    op.drop_column("llm_models", "input_price_per_million")
    op.drop_index("ix_llm_token_usages_session_id", table_name="llm_token_usages")
    op.drop_table("llm_token_usages")
