"""Persist approved Patrol provider identity and receipt without closing unknown effects."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0033patrol_effect_identity"
down_revision = "0032activity_lease_proof"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = [
        sa.Column("actuator_operation_id", sa.String(200), nullable=True),
        sa.Column("actuator_payload_digest", sa.String(64), nullable=True),
        sa.Column("actuator_target_uid", sa.String(200), nullable=True),
        sa.Column("actuator_target_ref", sa.String(255), nullable=True),
        sa.Column("actuator_receipt", JSONB(), nullable=True),
    ]
    actual = {c["name"]: c for c in sa.inspect(bind).get_columns("patrol_remediations")}
    for column in columns:
        if column.name not in actual:
            op.add_column("patrol_remediations", column)
            continue
        found = actual[column.name]
        valid = (
            isinstance(found["type"], JSONB)
            if isinstance(column.type, JSONB)
            else isinstance(found["type"], sa.String) and found["type"].length == column.type.length
        )
        if not valid or not found["nullable"]:
            raise RuntimeError(f"unexpected Patrol effect identity shape: {column.name}")
    # Existing partial index uses status NOT IN ('verified','failed','cancelled');
    # unknown intentionally remains active and blocks competing proposals.


def downgrade():
    raise NotImplementedError("provider identities cannot be discarded; rebuild a development DB")
