"""Independent metadata; frozen predecessor migrations never import new budget tables."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_budget_reservations = sa.Table(
    "evaluation_budget_reservations",
    metadata,
    sa.Column("call_identity", sa.UUID(), primary_key=True),
    sa.Column("scope_key", sa.Text(), nullable=False),
)
