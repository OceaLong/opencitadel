"""backfill existing rows to bootstrap admin owner

Revision ID: w1x2y3z4a5b6
Revises: v0w1x2y3z4a5
Create Date: 2026-07-02

"""
import os
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "w1x2y3z4a5b6"
down_revision: Union[str, Sequence[str], None] = "v0w1x2y3z4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OWNED_TABLES = (
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
    "questionnaires",
    "marketplace_fortune_predictions",
)


def _bootstrap_admin_email() -> str:
    return os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com").strip().lower()


def _bootstrap_admin_id(email: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-manus-bootstrap-admin:{email}"))


def upgrade() -> None:
    conn = op.get_bind()
    email = _bootstrap_admin_email()
    admin_id = _bootstrap_admin_id(email)
    username = email.split("@", 1)[0] or "admin"

    conn.execute(
        sa.text(
            """
            INSERT INTO users (
                id, email, username, password_hash, display_name, global_role, status, token_version
            )
            VALUES (
                :id, :email, :username, '', 'Administrator', 'admin', 'active', 0
            )
            ON CONFLICT (email) DO UPDATE
            SET global_role = 'admin',
                status = 'active',
                updated_at = CURRENT_TIMESTAMP(0)
            """
        ),
        {"id": admin_id, "email": email, "username": username},
    )

    for table_name in OWNED_TABLES:
        conn.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET owner_user_id = :admin_id
                WHERE owner_user_id IS NULL
                """
            ),
            {"admin_id": admin_id},
        )

    for table_name in ("llm_models", "skills"):
        conn.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET visibility = 'global', owner_user_id = NULL
                WHERE visibility IS NULL OR visibility = ''
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    email = _bootstrap_admin_email()
    admin_id = _bootstrap_admin_id(email)

    for table_name in OWNED_TABLES:
        conn.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET owner_user_id = NULL, team_id = NULL
                WHERE owner_user_id = :admin_id
                """
            ),
            {"admin_id": admin_id},
        )
