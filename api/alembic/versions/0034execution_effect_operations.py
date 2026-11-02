"""Event-derived effect indexes and durable recovery requests with tenant RLS."""

import sqlalchemy as sa

from alembic import op
from app.infrastructure.execution.effect_models import metadata

revision = "0034execution_effect_operations"
down_revision = "0033patrol_effect_identity"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    metadata.create_all(bind, checkfirst=False)
    valid = "public.opencitadel_authorization_valid()"
    scoped = "current_setting('app.auth_mode',true)='user' AND (current_setting('app.is_admin',true)='true' OR scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END)"
    system = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    for table in metadata.sorted_tables:
        name = quote(table.name)
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY effect_scope ON {name} TO {quote(api)},{quote(kernel)},{quote(owner)} USING ({valid} AND ({scoped} OR {system})) WITH CHECK ({valid} AND ({scoped} OR {system}))"
            )
        )
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO {quote(kernel)}"))
    bind.execute(sa.text(f"GRANT INSERT ON execution_effect_requests TO {quote(api)}"))
    bind.execute(
        sa.text(
            f"CREATE POLICY effect_request_ingress ON execution_effect_requests AS RESTRICTIVE FOR INSERT TO {quote(api)} WITH CHECK (status='pending' AND principal->>'user_id'=current_setting('app.user_id',true) AND kind IN ('query','resolution','abandon') AND command_type IS NULL AND command_payload_digest IS NULL AND claim_id IS NULL AND claim_until IS NULL AND attempts=0)"
        )
    )


def downgrade():
    raise RuntimeError("effect facts and evidence cannot be discarded; rebuild a development DB")
