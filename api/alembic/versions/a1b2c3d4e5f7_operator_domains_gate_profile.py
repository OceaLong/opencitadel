"""add operator_domains and gate_profile to sessions

Revision ID: a1b2c3d4e5f7
Revises: z4a5b6c7d8e9
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "z4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "operator_domains",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "gate_profile",
            sa.String(length=16),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "gate_profile")
    op.drop_column("sessions", "operator_domains")
