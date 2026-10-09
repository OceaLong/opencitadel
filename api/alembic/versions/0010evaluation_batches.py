"""Durable scoped evaluation orchestration; execution facts retain their original schemas."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0010evaluation_batches"
down_revision = "0009evaluation_budget"
branch_labels = None
depends_on = None
_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "evaluation_batches": f"""id uuid NOT NULL, suite_version uuid NOT NULL, principal jsonb NOT NULL, scope_body jsonb NOT NULL,
      revision bigint NOT NULL DEFAULT 1, status varchar(32) NOT NULL DEFAULT 'created', review_status varchar(16) NOT NULL DEFAULT 'not_required', cleanup_status varchar(16) NOT NULL DEFAULT 'clean',
      settings jsonb, parent_batch uuid, selected_slots jsonb, error varchar(128), generation bigint NOT NULL DEFAULT 0, claim_until timestamptz, last_tick timestamptz NOT NULL DEFAULT '-infinity', started_at timestamptz, deadline timestamptz, cancel_requested boolean NOT NULL DEFAULT false, dispatch_cursor integer NOT NULL DEFAULT -1 CHECK(dispatch_cursor BETWEEN -1 AND 4999),
      {_SCOPE}, PRIMARY KEY(scope_key,id), CHECK(status IN ('created','validating','rejected','queued','running','waiting','completed','completed_with_errors','failed','cancelling','cancelled')), CHECK(review_status IN ('not_required','pending','complete')), CHECK(cleanup_status IN ('clean','pending','failed'))""",
    "evaluation_batch_commands": f"""id uuid NOT NULL, batch_id uuid NOT NULL, request_id varchar(255) NOT NULL, kind varchar(16) NOT NULL, fingerprint varchar(64) NOT NULL, payload jsonb NOT NULL, applied boolean NOT NULL DEFAULT false,
      {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,kind,request_id), FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id), CHECK(kind IN ('start','cancel','retry_failed'))""",
    "evaluation_batch_results": f"""id uuid NOT NULL, batch_id uuid NOT NULL, case_revision_id uuid NOT NULL, config_version_id uuid NOT NULL, repetition integer NOT NULL CHECK(repetition BETWEEN 0 AND 4), ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 4999), revision bigint NOT NULL DEFAULT 1, attempt integer NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2), execution_status varchar(32) NOT NULL DEFAULT 'queued', scoring_status varchar(32) NOT NULL DEFAULT 'pending', unknown_effect boolean NOT NULL DEFAULT false, recovery_pending boolean NOT NULL DEFAULT false, error varchar(128), started_at timestamptz,
      {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,batch_id,case_revision_id,config_version_id,repetition), UNIQUE(scope_key,batch_id,ordinal), FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id)""",
    "evaluation_batch_attempts": f"""result_id uuid NOT NULL, attempt integer NOT NULL CHECK(attempt BETWEEN 0 AND 2), run_id uuid NOT NULL UNIQUE, command_id uuid NOT NULL UNIQUE, admission_key text NOT NULL UNIQUE, predecessor_run_id uuid, predecessor_generation bigint, available_at timestamptz NOT NULL DEFAULT '1970-01-01 00:00:00+00', intent jsonb, prepared_envelope jsonb, envelope jsonb, receipt jsonb, status varchar(32) NOT NULL DEFAULT 'intent', run_revision bigint NOT NULL DEFAULT 0, dispatched_at timestamptz, settled_at timestamptz, cancel_command_id uuid, cancel_sent boolean NOT NULL DEFAULT false,
      {_SCOPE}, PRIMARY KEY(scope_key,result_id,attempt), FOREIGN KEY(scope_key,result_id) REFERENCES evaluation_batch_results(scope_key,id)""",
    "evaluation_batch_events": f"""id uuid NOT NULL, batch_id uuid NOT NULL, revision bigint NOT NULL, kind varchar(32) NOT NULL, result_id uuid, evidence jsonb NOT NULL DEFAULT '{{}}',
      {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,batch_id,revision), FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id)""",
}
INDEXES = [
    "CREATE INDEX ix_evaluation_batches_claim ON evaluation_batches(last_tick,created_at) WHERE status NOT IN ('rejected','completed','completed_with_errors','failed','cancelled')",
    "CREATE INDEX ix_evaluation_batch_commands_pending ON evaluation_batch_commands(batch_id) WHERE NOT applied",
]


EFFECT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION public.opencitadel_e06_effect_unsafe(requested_scope text, requested_run uuid, include_unresolved boolean, encoded text, signature text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    proof jsonb; principal jsonb; secret text; expected bytea; supplied bytea;
    difference integer := 0; i integer;
BEGIN
    IF NOT public.opencitadel_authorization_valid() THEN RAISE EXCEPTION 'evaluation_effect_authorization_invalid'; END IF;
    IF current_setting('app.auth_mode',true)='system' THEN
        IF current_setting('app.system_actor',true) IS DISTINCT FROM 'execution-kernel' THEN RAISE EXCEPTION 'evaluation_effect_authorization_invalid'; END IF;
    ELSE
        IF current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
          OR requested_scope IS DISTINCT FROM (CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:' || current_setting('app.team_id',true) ELSE 'user:' || current_setting('app.user_id',true) END)
          OR encoded IS NULL OR octet_length(encoded)>65536 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'evaluation_effect_authorization_invalid'; END IF;
        SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
        expected := public.hmac(convert_to('opencitadel:e05:requester:v1:' || encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256');
        supplied := decode(signature,'hex');
        FOR i IN 0..31 LOOP difference := difference | (get_byte(expected,i) # get_byte(supplied,i)); END LOOP;
        IF difference<>0 THEN RAISE EXCEPTION 'evaluation_effect_authorization_invalid'; END IF;
        proof := encoded::jsonb; principal := proof->'principal';
        IF proof->>'kind' IS DISTINCT FROM 'user' OR proof->>'scope' IS DISTINCT FROM requested_scope
          OR proof->>'run_id' IS DISTINCT FROM requested_run::text OR proof->>'version' IS DISTINCT FROM '1'
          OR principal->>'user_id' IS DISTINCT FROM current_setting('app.user_id',true)
          OR (principal->>'global_role'='admin') IS DISTINCT FROM (current_setting('app.is_admin',true)='true')
          OR (principal->>'global_role'='auditor') IS DISTINCT FROM (current_setting('app.is_auditor',true)='true')
          THEN RAISE EXCEPTION 'evaluation_effect_authorization_invalid'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=principal->>'user_id' AND status='active' AND token_version=(principal->>'token_version')::integer AND global_role=principal->>'global_role')
          OR (requested_scope LIKE 'team:%' AND NOT EXISTS(SELECT 1 FROM public.team_members WHERE user_id=principal->>'user_id' AND team_id=substring(requested_scope FROM 6) AND role=principal->'team_roles'->>substring(requested_scope FROM 6)))
          THEN RAISE EXCEPTION 'evaluation_effect_requester_revoked'; END IF;
    END IF;
    IF requested_scope IS NULL OR requested_run IS NULL OR include_unresolved IS NULL OR NOT EXISTS(
      SELECT 1 FROM public.evaluation_batch_results r JOIN public.evaluation_batch_attempts a ON a.scope_key=r.scope_key AND a.result_id=r.id
      JOIN public.evaluation_batches b ON b.scope_key=r.scope_key AND b.id=r.batch_id
      WHERE r.scope_key=requested_scope AND a.run_id=requested_run
    ) THEN RAISE EXCEPTION 'evaluation_effect_association_unavailable'; END IF;
    RETURN (
WITH RECURSIVE history AS (
      SELECT r.id,r.batch_id,r.case_revision_id,r.config_version_id,r.repetition,r.unknown_effect,ARRAY[r.id] AS path,false AS cycle,0 AS depth
      FROM public.evaluation_batch_results r JOIN public.evaluation_batch_attempts a
        ON a.scope_key=r.scope_key AND a.result_id=r.id
      WHERE r.scope_key=requested_scope AND a.run_id=requested_run
      UNION
      SELECT p.id,p.batch_id,p.case_revision_id,p.config_version_id,p.repetition,p.unknown_effect,h.path || p.id,p.id=ANY(h.path),h.depth+1
      FROM history h JOIN public.evaluation_batches b ON b.scope_key=requested_scope AND b.id=h.batch_id
      JOIN public.evaluation_batch_results p ON p.scope_key=b.scope_key AND p.batch_id=b.parent_batch
        AND p.case_revision_id=h.case_revision_id AND p.config_version_id=h.config_version_id
        AND p.repetition=h.repetition WHERE NOT h.cycle AND h.depth<100
    ), attempts AS (
      SELECT a.run_id FROM public.evaluation_batch_attempts a JOIN history h ON a.result_id=h.id WHERE a.scope_key=requested_scope
      UNION SELECT CAST(requested_run AS uuid)
    ), runs AS (
      SELECT run_id FROM attempts
      UNION SELECT l.run_id FROM public.evaluation_run_lineages l JOIN public.evaluation_run_lineages origin
        ON l.root_run_id=origin.root_run_id
      JOIN attempts a ON a.run_id=origin.run_id
      JOIN public.evaluation_budget_bindings binding ON binding.run_id=l.run_id WHERE binding.scope_key=requested_scope
    )
    SELECT EXISTS(SELECT 1 FROM history WHERE unknown_effect OR cycle OR depth>=100)
      OR EXISTS(SELECT 1 FROM history h JOIN public.evaluation_batches b ON b.scope_key=requested_scope AND b.id=h.batch_id
        WHERE b.parent_batch IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.evaluation_batch_results p
          WHERE p.scope_key=requested_scope AND p.batch_id=b.parent_batch AND p.case_revision_id=h.case_revision_id
            AND p.config_version_id=h.config_version_id AND p.repetition=h.repetition))
      OR EXISTS(SELECT 1 FROM public.execution_model_dispatches d JOIN runs ON runs.run_id=d.run_id
        LEFT JOIN public.evaluation_budget_reservations r ON r.scope_key=d.scope_key AND CAST(r.call_identity AS text)=d.call_identity
        LEFT JOIN public.execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
        WHERE d.scope_key=requested_scope AND (r.state='unknown' OR (include_unresolved AND s.call_identity IS NULL)))
      OR EXISTS(SELECT 1 FROM public.evaluation_execution_leases l JOIN runs ON runs.run_id=l.run_id
        WHERE l.scope_key=requested_scope AND (l.state->>'failure_code'='NON_IDEMPOTENT_OUTCOME_UNKNOWN'
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'settled_activities','[]'::jsonb)) x WHERE x->>1='unknown')
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'activity_failure_codes','[]'::jsonb)) x WHERE x->>2='NON_IDEMPOTENT_OUTCOME_UNKNOWN')))
    );
END $$
"""


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    spec = importlib.util.spec_from_file_location(
        "e06_shape", Path(__file__).with_name("0002artifact_provenance.py")
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    existing = set(sa.inspect(bind).get_table_names(schema="public")) & TABLES.keys()
    if existing:
        helper._validate_existing(bind, TABLES, INDEXES, existing)
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not api or not kernel or api == kernel:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    valid = "opencitadel_authorization_valid()"
    system = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    scoped = "current_setting('app.auth_mode',true)='user' AND ((team_id IS NOT NULL AND team_id=NULLIF(current_setting('app.team_id',true),'')) OR (team_id IS NULL AND owner_user_id=NULLIF(current_setting('app.user_id',true),'')))"
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        for command in ("SELECT", "INSERT", "UPDATE"):
            predicate = (
                f"{valid} AND ({system} OR ({scoped}))"
                if command == "SELECT"
                or (
                    command == "INSERT"
                    and name in {"evaluation_batches", "evaluation_batch_commands"}
                )
                else f"{valid} AND ({system})"
            )
            clause = f"WITH CHECK ({predicate})" if command == "INSERT" else f"USING ({predicate})"
            bind.execute(
                sa.text(f"CREATE POLICY e06_{command.lower()} ON {name} FOR {command} {clause}")
            )
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE ON {name} TO {quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        columns = {
            "evaluation_batches": "id,suite_version,principal,scope_body,parent_batch,selected_slots,owner_user_id,team_id,created_by",
            "evaluation_batch_commands": "id,batch_id,request_id,kind,fingerprint,payload,owner_user_id,team_id,created_by",
        }.get(name)
        if columns is not None:
            bind.execute(sa.text(f"GRANT INSERT({columns}) ON {name} TO {quote(api)}"))
    for ddl in INDEXES:
        bind.execute(sa.text(ddl.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)))

    bind.execute(sa.text(EFFECT_FUNCTION))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_e06_effect_unsafe(text,uuid,boolean,text,text) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e06_effect_unsafe(text,uuid,boolean,text,text) TO {quote(api)},{quote(kernel)}"
        )
    )

    bind.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION public.opencitadel_e06_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE mutable text[];
BEGIN
    IF TG_TABLE_NAME='evaluation_batch_events' THEN
        RAISE EXCEPTION 'evaluation_history_immutable';
    ELSIF TG_TABLE_NAME='evaluation_batch_commands' THEN
        mutable := ARRAY['applied'];
    ELSIF TG_TABLE_NAME='evaluation_batches' THEN
        mutable := ARRAY['revision','status','review_status','cleanup_status','error','generation','claim_until','last_tick','started_at','deadline','cancel_requested','dispatch_cursor','settings'];
        IF OLD.settings IS NOT NULL AND OLD.settings IS DISTINCT FROM NEW.settings THEN
            RAISE EXCEPTION 'evaluation_settings_immutable';
        END IF;
    ELSIF TG_TABLE_NAME='evaluation_batch_results' THEN
        mutable := ARRAY['revision','attempt','execution_status','scoring_status','unknown_effect','recovery_pending','error','started_at'];
        IF (OLD.unknown_effect AND NOT NEW.unknown_effect) OR NEW.attempt<OLD.attempt OR (OLD.started_at IS NOT NULL AND OLD.started_at IS DISTINCT FROM NEW.started_at) THEN
            RAISE EXCEPTION 'evaluation_result_history_immutable';
        END IF;
    ELSE
        mutable := ARRAY['intent','prepared_envelope','envelope','receipt','status','run_revision','dispatched_at','settled_at','cancel_command_id','cancel_sent'];
        IF (OLD.intent IS NOT NULL AND OLD.intent IS DISTINCT FROM NEW.intent)
           OR (OLD.prepared_envelope IS NOT NULL AND OLD.prepared_envelope IS DISTINCT FROM NEW.prepared_envelope)
           OR (OLD.envelope IS NOT NULL AND OLD.envelope IS DISTINCT FROM NEW.envelope)
           OR NEW.run_revision<OLD.run_revision THEN
            RAISE EXCEPTION 'evaluation_admission_immutable';
        END IF;
    END IF;
    mutable := array_append(mutable, 'scope_key');
    IF (to_jsonb(OLD)-mutable) IS DISTINCT FROM (to_jsonb(NEW)-mutable) THEN
        RAISE EXCEPTION 'evaluation_identity_immutable';
    END IF;
    RETURN NEW;
END $$
""")
    )
    for name in TABLES:
        bind.execute(
            sa.text(
                f"CREATE TRIGGER e06_immutable BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e06_immutable()"
            )
        )


def downgrade():
    raise RuntimeError("evaluation orchestration history cannot be discarded")
