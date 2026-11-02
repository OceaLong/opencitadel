"""Retain worker ownership on durable command delivery."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0032activity_lease_proof"
down_revision = "0031phase1_embedding_space"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("execution_command_inbox")
    }
    column = columns.get("activity_lease_proof")
    if column is None:
        op.add_column(
            "execution_command_inbox", sa.Column("activity_lease_proof", JSONB(), nullable=True)
        )
    elif not isinstance(column["type"], JSONB) or not column["nullable"]:
        raise RuntimeError("unexpected Activity lease proof column shape")


def downgrade():
    raise NotImplementedError("worker fencing proofs cannot be discarded")
