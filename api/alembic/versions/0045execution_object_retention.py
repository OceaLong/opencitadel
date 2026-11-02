"""Private object intents and irreversible collection fences, not execution facts."""

import sqlalchemy as sa

from alembic import op

revision = "0045execution_object_retention"
down_revision = "0044definition_management"
branch_labels = None
depends_on = None


FUNCTIONS = {
    "opencitadel_object_register(text,uuid,text,text)": """
CREATE FUNCTION public.opencitadel_object_register(scope_value text,run_value uuid,key_value text,digest_value text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actual_scope text; current_state text;
BEGIN
 IF scope_value IS NULL THEN
   SELECT scope_key INTO scope_value FROM public.execution_object_runs WHERE run_id=run_value;
 END IF;
 IF scope_value IS NULL OR NOT public.opencitadel_context_scope_allowed(scope_value)
 THEN RAISE EXCEPTION 'object_scope_denied' USING ERRCODE='42501'; END IF;
 IF digest_value !~ '^[0-9a-f]{64}$'
 OR key_value !~ '^execution/(inputs|results|context_documents|context_sources)/[0-9a-f-]{36}/([0-9a-f-]{36}/)?[0-9a-f]{64}\\.(json|bin|txt)$'
 OR split_part(split_part(key_value,'/',array_length(string_to_array(key_value,'/'),1)),'.',1)<>digest_value
 OR (split_part(key_value,'/',2) IN ('inputs','context_sources') AND split_part(key_value,'/',3)<>run_value::text)
 THEN RAISE EXCEPTION 'object_identity_invalid'; END IF;
 INSERT INTO public.execution_object_runs(run_id,scope_key) VALUES(run_value,scope_value) ON CONFLICT DO NOTHING;
 SELECT scope_key,state INTO actual_scope,current_state FROM public.execution_object_runs WHERE run_id=run_value FOR UPDATE;
 IF actual_scope IS DISTINCT FROM scope_value THEN RAISE EXCEPTION 'object_scope_denied' USING ERRCODE='42501'; END IF;
 IF current_state<>'live' THEN RETURN false; END IF;
 INSERT INTO public.execution_objects(object_key,run_id,scope_key,digest)
 VALUES(key_value,run_value,scope_value,digest_value) ON CONFLICT DO NOTHING;
 RETURN EXISTS(SELECT 1 FROM public.execution_objects WHERE object_key=key_value
   AND run_id=run_value AND scope_key=scope_value AND digest=digest_value AND cleaned_at IS NULL);
END $$;
""",
    "opencitadel_object_ready(text)": """
CREATE FUNCTION public.opencitadel_object_ready(key_value text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE current_state text; identity uuid; scope_value text;
BEGIN
 SELECT run_id,scope_key INTO identity,scope_value FROM public.execution_objects WHERE object_key=key_value;
 IF scope_value IS NULL OR NOT public.opencitadel_context_scope_allowed(scope_value) THEN RETURN false; END IF;
 SELECT state INTO current_state FROM public.execution_object_runs WHERE run_id=identity FOR UPDATE;
 IF current_state<>'live' THEN RETURN false; END IF;
 UPDATE public.execution_objects SET ready_at=COALESCE(ready_at,clock_timestamp()) WHERE object_key=key_value AND cleaned_at IS NULL;
 RETURN FOUND;
END $$;
""",
    "opencitadel_object_available(text)": """
CREATE FUNCTION public.opencitadel_object_available(key_value text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.execution_objects o JOIN public.execution_object_runs r USING(run_id,scope_key)
 WHERE o.object_key=key_value AND o.ready_at IS NOT NULL AND o.cleaned_at IS NULL AND r.state='live'
 AND public.opencitadel_context_scope_allowed(o.scope_key))
$$;
""",
    "opencitadel_object_collection(text,uuid)": """
CREATE FUNCTION public.opencitadel_object_collection(scope_value text,run_value uuid)
RETURNS TABLE(run_id uuid) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 -- Session nodes avoid the quadratic all-pairs expansion of a long history.
 WITH RECURSIVE related(kind,identity) AS (
   SELECT 'run'::text,run_value::text WHERE public.opencitadel_context_scope_allowed(scope_value)
   UNION
   SELECT neighbor.kind,neighbor.identity FROM related r JOIN LATERAL (
     SELECT 'run'::text kind,public_payload->>'parent_run_id' identity FROM public.execution_events
       WHERE r.kind='run' AND owner_scope_key=scope_value AND stream_type='run' AND stream_version=1
       AND stream_id=r.identity AND public_payload->>'parent_run_id' IS NOT NULL
     UNION
     SELECT 'run'::text,stream_id FROM public.execution_events
       WHERE r.kind='run' AND owner_scope_key=scope_value AND stream_type='run' AND stream_version=1
       AND public_payload->>'parent_run_id'=r.identity
     UNION
     SELECT 'session'::text,public_payload->>'source_entity_id' FROM public.execution_events
       WHERE r.kind='run' AND owner_scope_key=scope_value AND stream_type='run' AND stream_version=1
       AND stream_id=r.identity AND public_payload->>'source_entity_type'='session'
     UNION
     SELECT 'run'::text,stream_id FROM public.execution_events
       WHERE r.kind='session' AND owner_scope_key=scope_value AND stream_type='run' AND stream_version=1
       AND public_payload->>'source_entity_type'='session' AND public_payload->>'source_entity_id'=r.identity
   ) neighbor ON true
 ) SELECT identity::uuid FROM related WHERE kind='run'
$$;
""",
    "opencitadel_object_reference_guard(text,uuid)": """
CREATE FUNCTION public.opencitadel_object_reference_guard(scope_value text,run_value uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT public.opencitadel_context_scope_allowed(scope_value) THEN RETURN false; END IF;
 -- Same scope append lock as PostgresEventStore; never lock an upload intent here.
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('scope','UTF8')||decode('00','hex')||convert_to(scope_value,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 IF EXISTS(SELECT 1 FROM public.execution_object_runs WHERE run_id=run_value AND (scope_key<>scope_value OR state<>'live')) THEN RETURN false; END IF;
 -- New admission has no formal predecessors; its caller holds this scope
 -- lock through inbox publication. Avoid scanning history on this hot path.
 IF NOT EXISTS(SELECT 1 FROM public.execution_events WHERE owner_scope_key=scope_value
   AND stream_type='run' AND stream_id=run_value::text AND stream_version=1) THEN RETURN true; END IF;
 -- The same formal closure is used for retention and new references.
 RETURN NOT EXISTS(SELECT 1 FROM public.opencitadel_object_collection(scope_value,run_value) c
 JOIN public.execution_object_runs r ON r.run_id=c.run_id
 WHERE r.scope_key=scope_value AND r.state<>'live');
END $$;
""",
}


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    quote = bind.dialect.identifier_preparer.quote
    op.execute("""
CREATE INDEX ix_execution_origins_parent ON execution_events(owner_scope_key,(public_payload->>'parent_run_id'))
 WHERE stream_type='run' AND stream_version=1;
CREATE INDEX ix_execution_origins_session ON execution_events(owner_scope_key,(public_payload->>'source_entity_id'))
 WHERE stream_type='run' AND stream_version=1 AND public_payload->>'source_entity_type'='session';
CREATE TABLE execution_object_runs(
 run_id uuid PRIMARY KEY,scope_key varchar(261) NOT NULL CHECK(scope_key ~ '^(user|team):.{1,255}$'),
 state varchar(16) NOT NULL DEFAULT 'live' CHECK(state IN ('live','deleting','deleted')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),checked_at timestamptz,
 deleting_at timestamptz,reason varchar(64),UNIQUE(run_id,scope_key),
 CHECK((state='live' AND deleting_at IS NULL) OR (state<>'live' AND deleting_at IS NOT NULL))
);
CREATE INDEX ix_execution_object_run_check ON execution_object_runs(checked_at NULLS FIRST,run_id) WHERE state='live';
CREATE TABLE execution_objects(
 object_key text PRIMARY KEY,run_id uuid NOT NULL,scope_key varchar(261) NOT NULL,
 digest varchar(64) NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),ready_at timestamptz,cleaned_at timestamptz,delete_attempt_at timestamptz,
 FOREIGN KEY(run_id,scope_key) REFERENCES execution_object_runs(run_id,scope_key) ON DELETE RESTRICT
);
CREATE INDEX ix_execution_objects_run ON execution_objects(run_id,object_key);
CREATE INDEX ix_execution_objects_clean ON execution_objects(cleaned_at NULLS FIRST,object_key);
""")
    op.execute("""
CREATE FUNCTION public.opencitadel_object_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.run_id<>OLD.run_id OR NEW.scope_key<>OLD.scope_key OR NEW.created_at<>OLD.created_at
 THEN RAISE EXCEPTION 'object_identity_immutable'; END IF;
 IF TG_TABLE_NAME='execution_object_runs' THEN
   IF (OLD.state='deleted' AND NEW.state<>'deleted') OR (OLD.state='deleting' AND NEW.state='live')
   THEN RAISE EXCEPTION 'object_tombstone_immutable'; END IF;
 ELSE
   IF NEW.object_key<>OLD.object_key OR NEW.digest<>OLD.digest
   OR (OLD.ready_at IS NOT NULL AND NEW.ready_at IS DISTINCT FROM OLD.ready_at)
   OR (OLD.cleaned_at IS NOT NULL AND (NEW.cleaned_at IS NULL OR NEW.cleaned_at<OLD.cleaned_at))
   THEN RAISE EXCEPTION 'object_identity_immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.opencitadel_object_immutable() FROM PUBLIC;
CREATE TRIGGER execution_object_run_immutable BEFORE UPDATE ON execution_object_runs
 FOR EACH ROW EXECUTE FUNCTION public.opencitadel_object_immutable();
CREATE TRIGGER execution_object_immutable BEFORE UPDATE ON execution_objects
 FOR EACH ROW EXECUTE FUNCTION public.opencitadel_object_immutable();
""")
    for table in ("execution_object_runs", "execution_objects"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        op.execute(
            f"CREATE POLICY object_owner ON {table} FOR ALL TO {quote(owner)} USING(public.opencitadel_context_scope_allowed(scope_key)) WITH CHECK(public.opencitadel_context_scope_allowed(scope_key))"
        )
        trusted = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
        op.execute(
            f"CREATE POLICY object_kernel ON {table} FOR ALL TO {quote(kernel)} USING({trusted}) WITH CHECK({trusted})"
        )
        op.execute(f"GRANT SELECT,UPDATE ON {table} TO {quote(kernel)}")
    for signature, ddl in FUNCTIONS.items():
        op.execute(ddl)
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {quote(api)},{quote(kernel)}")


def downgrade():
    raise RuntimeError("object tombstones are retained; rebuild an owned development database")
