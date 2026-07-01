"""add ownership and visibility columns

Revision ID: v0w1x2y3z4a5
Revises: u9v0w1x2y3z4
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "v0w1x2y3z4a5"
down_revision: Union[str, Sequence[str], None] = "u9v0w1x2y3z4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OWNED_TABLES = (
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
)


def _add_owner_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("owner_user_id", sa.String(255), nullable=True))
    op.add_column(table_name, sa.Column("team_id", sa.String(255), nullable=True))
    op.create_foreign_key(
        f"fk_{table_name}_owner_user_id",
        table_name,
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        f"fk_{table_name}_team_id",
        table_name,
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_{table_name}_owner_user_id", table_name, ["owner_user_id"])
    op.create_index(f"ix_{table_name}_team_id", table_name, ["team_id"])


def _drop_owner_columns(table_name: str) -> None:
    op.drop_index(f"ix_{table_name}_team_id", table_name=table_name)
    op.drop_index(f"ix_{table_name}_owner_user_id", table_name=table_name)
    op.drop_constraint(f"fk_{table_name}_team_id", table_name, type_="foreignkey")
    op.drop_constraint(f"fk_{table_name}_owner_user_id", table_name, type_="foreignkey")
    op.drop_column(table_name, "team_id")
    op.drop_column(table_name, "owner_user_id")


def upgrade() -> None:
    for table_name in OWNED_TABLES:
        _add_owner_columns(table_name)

    op.create_index(
        "ix_llm_token_usages_owner_created_at",
        "llm_token_usages",
        ["owner_user_id", "created_at"],
    )

    for table_name in ("llm_models", "skills"):
        op.add_column(table_name, sa.Column("owner_user_id", sa.String(255), nullable=True))
        op.add_column(
            table_name,
            sa.Column("visibility", sa.String(32), nullable=False, server_default=sa.text("'global'")),
        )
        op.create_foreign_key(
            f"fk_{table_name}_owner_user_id",
            table_name,
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table_name}_owner_user_id", table_name, ["owner_user_id"])
        op.create_index(f"ix_{table_name}_visibility", table_name, ["visibility"])

    for table_name in ("questionnaires", "marketplace_fortune_predictions"):
        op.add_column(table_name, sa.Column("owner_user_id", sa.String(255), nullable=True))
        op.create_foreign_key(
            f"fk_{table_name}_owner_user_id",
            table_name,
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table_name}_owner_user_id", table_name, ["owner_user_id"])


def downgrade() -> None:
    for table_name in ("marketplace_fortune_predictions", "questionnaires"):
        op.drop_index(f"ix_{table_name}_owner_user_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_owner_user_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "owner_user_id")

    for table_name in ("skills", "llm_models"):
        op.drop_index(f"ix_{table_name}_visibility", table_name=table_name)
        op.drop_index(f"ix_{table_name}_owner_user_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_owner_user_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "visibility")
        op.drop_column(table_name, "owner_user_id")

    op.drop_index("ix_llm_token_usages_owner_created_at", table_name="llm_token_usages")
    for table_name in reversed(OWNED_TABLES):
        _drop_owner_columns(table_name)
