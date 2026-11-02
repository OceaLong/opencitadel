"""Scoped operational version availability without exposing worker inventories."""

import sqlalchemy as sa

from alembic import op

revision = "0041definition_availability"
down_revision = "0040context_cut_checkpoint"
branch_labels = None
depends_on = None

SIGNATURE = "public.opencitadel_definition_availability(text,uuid)"
FUNCTION = """
CREATE FUNCTION public.opencitadel_definition_availability(scope_value text,run_value uuid)
RETURNS TABLE(state text,definition_version integer,definition_digest text,checked_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN r.terminal THEN 'terminal'
   WHEN r.definition_digest IS NULL THEN 'unversioned'
   WHEN d.retired_at IS NULL AND d.manifest_digest IS NOT NULL AND EXISTS (
     SELECT 1 FROM public.execution_worker_capabilities w
     WHERE w.expires_at>clock_timestamp() AND EXISTS (
       SELECT 1 FROM jsonb_array_elements(w.body->'definitions') p
       WHERE p->>'manifest_digest'=r.definition_digest
     )
   ) THEN 'available' ELSE 'version_unavailable' END,
   d.definition_version,r.definition_digest::text,clock_timestamp()
 FROM public.execution_run_projection r
 LEFT JOIN public.execution_definitions d ON d.manifest_digest=r.definition_digest
 WHERE r.run_id=run_value
   AND scope_value=CASE WHEN r.team_id IS NOT NULL THEN 'team:'||r.team_id
                       ELSE 'user:'||r.owner_user_id END
   AND public.opencitadel_authorization_valid()
   AND ((current_setting('app.auth_mode',true)='user' AND scope_value=
     CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>''
       THEN 'team:'||current_setting('app.team_id',true)
       ELSE 'user:'||current_setting('app.user_id',true) END)
     OR (current_setting('app.auth_mode',true)='system'
       AND current_setting('app.system_actor',true)='execution-kernel'))
$$;
"""


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),"
            "current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    # Only this function's owner can inspect capabilities for a non-admin read.
    # API users retain no access to worker identities, builds or complete bodies.
    bind.execute(
        sa.text(
            "CREATE POLICY definition_availability_read ON execution_worker_capabilities "
            f"FOR SELECT TO {quote(owner)} USING (public.opencitadel_authorization_valid() "
            "AND current_setting('app.auth_mode',true)='user')"
        )
    )
    bind.execute(sa.text(FUNCTION))
    bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC"))
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {quote(api)},{quote(kernel)}"))


def downgrade():
    op.execute(f"DROP FUNCTION {SIGNATURE}")
    op.execute("DROP POLICY definition_availability_read ON execution_worker_capabilities")
