"""Forward migration owns recording tables, independently of frozen Base metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_recording_versions = sa.Table(
    "evaluation_recording_versions",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
)
