"""Independent environment metadata; historical Base migrations remain frozen."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_environment_leases = sa.Table(
    "evaluation_environment_leases",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
)
