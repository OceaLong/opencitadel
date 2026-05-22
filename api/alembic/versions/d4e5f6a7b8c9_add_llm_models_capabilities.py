"""add llm_models capabilities

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-22 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "capabilities",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE llm_models
        SET capabilities = jsonb_build_object(
            'vision', supports_multimodal,
            'vision_with_tools', true,
            'max_image_bytes', 5242880,
            'max_images_per_request', 8,
            'image_encoding', 'data_url'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("llm_models", "capabilities")
