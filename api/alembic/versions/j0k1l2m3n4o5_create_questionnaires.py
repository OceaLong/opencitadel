"""create questionnaires tables

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, Sequence[str], None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "questionnaires",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("questions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("manage_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_questionnaires_slug", "questionnaires", ["slug"], unique=True)

    op.create_table(
        "questionnaire_responses",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("questionnaire_id", sa.String(255), sa.ForeignKey("questionnaires.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("respondent_name", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
    )
    op.create_index("ix_questionnaire_responses_qid", "questionnaire_responses", ["questionnaire_id"])


def downgrade() -> None:
    op.drop_index("ix_questionnaire_responses_qid", table_name="questionnaire_responses")
    op.drop_table("questionnaire_responses")
    op.drop_index("ix_questionnaires_slug", table_name="questionnaires")
    op.drop_table("questionnaires")
