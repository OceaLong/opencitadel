#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""add skill progressive disclosure columns

Revision ID: ee04skillprompts
Revises: dd03mcpurlenc
Create Date: 2026-07-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ee04skillprompts"
down_revision: Union[str, None] = "dd03mcpurlenc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "skills",
        sa.Column(
            "resources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "override_base_rules",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "auto_recommend",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "source_format",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'native'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("skills", "source_format")
    op.drop_column("skills", "auto_recommend")
    op.drop_column("skills", "override_base_rules")
    op.drop_column("skills", "resources")
    op.drop_column("skills", "body")
