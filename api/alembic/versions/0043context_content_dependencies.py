"""Copied content retains immutable original resource dependencies."""

from alembic import op

revision = "0043context_content_dependencies"
down_revision = "0042context_request_lookup"
branch_labels = None
depends_on = None


def upgrade():
    # Forward-owned column: historical greenfield shape guards keep their schema.
    # The existing immutable trigger and role privileges also cover this column.
    op.execute("""
ALTER TABLE execution_public_content ADD COLUMN IF NOT EXISTS
 context_dependencies jsonb NOT NULL DEFAULT '[]'::jsonb
""")
    op.execute("""
ALTER TABLE execution_public_content ADD CONSTRAINT context_dependencies_bounded
 CHECK (jsonb_typeof(context_dependencies)='array' AND jsonb_array_length(context_dependencies)<=4096)
""")
    op.execute("""
CREATE INDEX ix_execution_content_context_source
 ON execution_public_content(scope_key,activity_id,generation) WHERE phase='output'
""")


def downgrade():
    raise RuntimeError(
        "copied content authority is immutable; rebuild an owned development database"
    )
