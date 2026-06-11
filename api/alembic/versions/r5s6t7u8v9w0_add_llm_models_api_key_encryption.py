"""add llm_models api_key_encryption column

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-06-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r5s6t7u8v9w0"
down_revision: Union[str, None] = "q4r5s6t7u8v9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _looks_like_fernet_token(value: str) -> bool:
    import base64
    import re

    if not value or len(value) < 44:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+=*", value):
        return False
    try:
        padding = b"=" * ((4 - len(value) % 4) % 4)
        raw = base64.urlsafe_b64decode(value.encode() + padding)
    except Exception:
        return False
    return len(raw) >= 57 and raw[0] == 0x80


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "api_key_encryption",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy_plaintext'"),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, api_key FROM llm_models WHERE api_key != ''")
    ).fetchall()

    for row_id, api_key in rows:
        encryption = "fernet_v1" if _looks_like_fernet_token(api_key) else "legacy_plaintext"
        conn.execute(
            sa.text(
                "UPDATE llm_models SET api_key_encryption = :encryption WHERE id = :id"
            ),
            {"encryption": encryption, "id": row_id},
        )


def downgrade() -> None:
    op.drop_column("llm_models", "api_key_encryption")
