"""Immutable code definitions, request pins and expiring worker capabilities."""

import sqlalchemy as sa

from alembic import op
from app.infrastructure.execution.definition_models import metadata

revision = "0036execution_definitions"
down_revision = "0035tool_intent_reads"
branch_labels = None
depends_on = None

PIN_FUNCTION = """
CREATE FUNCTION public.opencitadel_pin_definition(scope_value text, command_value uuid, run_value uuid, family_value text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE chosen public.execution_definitions; existing text; existing_run uuid;
BEGIN
 IF NOT COALESCE(public.opencitadel_authorization_valid(),false) OR NOT COALESCE((
   (current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')
   OR (current_setting('app.auth_mode',true)='user' AND scope_value=CASE
     WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true)
     ELSE 'user:'||current_setting('app.user_id',true) END)),false)
 THEN RAISE EXCEPTION 'definition_scope_denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('definition-admit:'||scope_value||':'||command_value::text,0));
 SELECT manifest_digest,run_id INTO existing,existing_run FROM public.execution_definition_admissions
 WHERE scope_key=scope_value AND command_id=command_value;
 IF existing IS NOT NULL THEN
   IF existing_run<>run_value THEN RAISE EXCEPTION 'definition_request_conflict'; END IF;
   SELECT * INTO chosen FROM public.execution_definitions WHERE manifest_digest=existing;
   IF chosen.family<>family_value THEN RAISE EXCEPTION 'definition_request_conflict'; END IF;
   RETURN jsonb_build_object('manifest',chosen.body,'digest',chosen.manifest_digest);
 END IF;
 PERFORM 1 FROM public.execution_definition_heads WHERE family=family_value FOR SHARE;
 SELECT d.* INTO chosen FROM public.execution_definitions d JOIN public.execution_definition_heads h
 ON h.manifest_digest=d.manifest_digest WHERE h.family=family_value FOR SHARE OF d;
 IF NOT FOUND OR chosen.retired_at IS NOT NULL THEN RAISE EXCEPTION 'definition_unavailable'; END IF;
 INSERT INTO public.execution_definition_admissions(scope_key,command_id,run_id,manifest_digest)
 VALUES(scope_value,command_value,run_value,chosen.manifest_digest);
 RETURN jsonb_build_object('manifest',chosen.body,'digest',chosen.manifest_digest);
END $$;
"""


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
    for table, column, sql_type in (
        ("execution_activity_tasks", "definition_digest", "varchar(64)"),
        ("execution_activity_tasks", "handler_version", "varchar(96)"),
        ("execution_run_projection", "definition_digest", "varchar(64)"),
    ):
        bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"))
    bind.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_activity_definition_route ON execution_activity_tasks (definition_digest,activity_type,handler_version,status,available_at)"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_run_definition_route ON execution_run_projection (definition_digest,decision_due_at) WHERE terminal=false"
        )
    )
    valid = "public.opencitadel_authorization_valid()"
    system = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    user = "current_setting('app.auth_mode',true)='user'"
    admin = f"{user} AND current_setting('app.is_admin',true)='true'"
    for table in metadata.sorted_tables:
        name = quote(table.name)
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        read = (
            user if table.name in {"execution_definitions", "execution_definition_heads"} else admin
        )
        if table.name == "execution_definition_admissions":
            read = f"{user} AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
        bind.execute(
            sa.text(
                f"CREATE POLICY definition_read ON {name} FOR SELECT TO {quote(api)},{quote(kernel)},{quote(owner)} USING ({valid} AND (({read}) OR ({system})))"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY definition_kernel ON {name} FOR ALL TO {quote(kernel)},{quote(owner)} USING ({valid} AND ({system})) WITH CHECK ({valid} AND ({system}))"
            )
        )
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO {quote(kernel)}"))
    # Head publication is administrative. Manifest bodies remain kernel-owned.
    for name in ("execution_definition_heads", "execution_definition_changes"):
        bind.execute(
            sa.text(
                f"CREATE POLICY definition_admin ON {name} FOR ALL TO {quote(api)},{quote(owner)} USING ({valid} AND ({admin})) WITH CHECK ({valid} AND ({admin}))"
            )
        )
        bind.execute(sa.text(f"GRANT INSERT,UPDATE ON {name} TO {quote(api)}"))
    # SELECT FOR SHARE also needs UPDATE USING under RLS. This owner-only
    # policy permits the lock in the fixed SECURITY DEFINER function, while
    # WITH CHECK false still rejects every actual user-context row update.
    for name in ("execution_definitions", "execution_definition_heads"):
        bind.execute(
            sa.text(
                f"CREATE POLICY definition_pin_lock ON {name} FOR UPDATE TO {quote(owner)} USING ({valid} AND ({user})) WITH CHECK (false)"
            )
        )
    scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
    bind.execute(
        sa.text(
            f"CREATE POLICY definition_pin_ingress ON execution_definition_admissions FOR INSERT TO {quote(owner)} WITH CHECK ({valid} AND ({user}) AND {scoped})"
        )
    )
    # The function can lock a public head without giving API callers UPDATE on
    # immutable manifests. No caller-provided version or digest is accepted.
    bind.execute(sa.text(PIN_FUNCTION))
    signature = "public.opencitadel_pin_definition(text,uuid,uuid,text)"
    bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quote(api)},{quote(kernel)}"))
    bind.execute(
        sa.text(
            "REVOKE UPDATE,DELETE ON execution_definition_changes FROM "
            + quote(kernel)
            + ","
            + quote(api)
        )
    )
    bind.execute(
        sa.text("""
CREATE FUNCTION public.opencitadel_definition_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'definition_identity_immutable'; END IF;
 IF NEW.family IS DISTINCT FROM OLD.family OR NEW.definition_version IS DISTINCT FROM OLD.definition_version
 OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest OR NEW.body IS DISTINCT FROM OLD.body
 OR NEW.created_at IS DISTINCT FROM OLD.created_at OR (OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at)
 THEN RAISE EXCEPTION 'definition_identity_immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER definition_immutable BEFORE UPDATE OR DELETE ON public.execution_definitions
FOR EACH ROW EXECUTE FUNCTION public.opencitadel_definition_immutable();
""")
    )


def downgrade():
    raise RuntimeError("definition pins cannot be erased; rebuild an owned development database")
