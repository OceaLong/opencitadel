"""Bound historical context request recovery independently of queue retention."""

from alembic import op

revision = "0042context_request_lookup"
down_revision = "0041definition_availability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE INDEX ix_execution_context_activity_request
 ON execution_events(owner_scope_key,stream_id,(public_payload->>'activity_id'),(public_payload->>'generation'))
 WHERE stream_type='run' AND event_type='ActivityRequested'
""")
    op.execute("""
CREATE INDEX ix_execution_context_effect_source
 ON execution_events(owner_scope_key,stream_id,position)
 WHERE stream_type='run' AND event_type='EffectOperationPrepared'
""")


def downgrade():
    op.execute("DROP INDEX ix_execution_context_effect_source")
    op.execute("DROP INDEX ix_execution_context_activity_request")
