"""merge migration heads

Revision ID: m3n4o5p6q7r8
Revises: d0e1f2a3b4c5, l2m3n4o5p6q7
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: Union[str, Sequence[str], None] = ("d0e1f2a3b4c5", "l2m3n4o5p6q7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
