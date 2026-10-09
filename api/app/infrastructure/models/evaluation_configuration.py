"""Forward-owned handles, deliberately separate from frozen greenfield Base metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_config_versions = sa.Table(
    "evaluation_config_versions",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
)
