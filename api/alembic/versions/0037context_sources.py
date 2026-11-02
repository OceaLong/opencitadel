"""Narrow private source references and immutable request-bound context cuts."""

import sqlalchemy as sa

from alembic import op

revision = "0037context_sources"
down_revision = "0036execution_definitions"
branch_labels = None
depends_on = None

FUNCTIONS = {
    "public.opencitadel_context_scope_allowed(text)": """
CREATE FUNCTION public.opencitadel_context_scope_allowed(scope_value text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(public.opencitadel_authorization_valid() AND (
   (current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')
   OR (current_setting('app.auth_mode',true)='user'
     AND EXISTS (SELECT 1 FROM public.users u WHERE u.id=current_setting('app.user_id',true) AND u.status='active')
     AND ((scope_value='user:'||current_setting('app.user_id',true) AND COALESCE(current_setting('app.team_id',true),'')='')
       OR (scope_value='team:'||current_setting('app.team_id',true)
         AND EXISTS (SELECT 1 FROM public.team_members m WHERE m.team_id=current_setting('app.team_id',true)
           AND m.user_id=current_setting('app.user_id',true)))))
 ),false)
$$;
""",
    "public.opencitadel_context_cut(text,text,uuid)": """
CREATE FUNCTION public.opencitadel_context_cut(scope_value text,collection_value text,request_value uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE saved jsonb; position_value bigint;
BEGIN
 IF NOT public.opencitadel_context_scope_allowed(scope_value) OR length(collection_value) NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'context_scope_denied' USING ERRCODE='42501'; END IF;
 IF request_value IS NOT NULL THEN
   PERFORM pg_advisory_xact_lock(hashtextextended('context-cut:'||scope_value||':'||request_value::text,0));
   SELECT body INTO saved FROM public.execution_context_cuts WHERE scope_key=scope_value AND request_id=request_value;
   IF saved IS NOT NULL THEN
     IF saved->>'collection_id'<>collection_value THEN RAISE EXCEPTION 'context_request_conflict'; END IF;
     RETURN saved;
   END IF;
 END IF;
 SELECT head_position INTO position_value FROM public.execution_scope_head WHERE owner_scope_key=scope_value;
 saved=jsonb_build_object('format_version',1,'scope_key',scope_value,'collection_id',collection_value,
                         'through_position',COALESCE(position_value,0));
 IF request_value IS NOT NULL THEN
   INSERT INTO public.execution_context_cuts(scope_key,request_id,body) VALUES(scope_value,request_value,saved);
 END IF;
 RETURN saved;
END $$;
""",
    "public.opencitadel_context_source_page(text,text,bigint,bigint,integer)": """
CREATE FUNCTION public.opencitadel_context_source_page(scope_value text,collection_value text,through_value bigint,
 after_value bigint,limit_value integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result_value jsonb;
BEGIN
 IF NOT public.opencitadel_context_scope_allowed(scope_value)
 THEN RAISE EXCEPTION 'context_scope_denied' USING ERRCODE='42501'; END IF;
 IF limit_value NOT BETWEEN 1 AND 201 OR after_value<0 OR through_value<after_value OR length(collection_value) NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'context_page_invalid'; END IF;
 SELECT COALESCE(jsonb_agg(value ORDER BY position),'[]'::jsonb) INTO result_value FROM (
   SELECT e.position,jsonb_build_object(
     'source_id','formal:'||e.event_id::text,'run_id',e.stream_id,'formal_position',e.position,
     'event_hash',e.event_hash,'kind',CASE WHEN e.event_type='RunCreated' THEN 'user' ELSE 'assistant' END,
     'object_ref',CASE WHEN e.event_type='RunCreated' THEN e.internal_payload->'semantic_payload'->>'input_ref'
                      ELSE e.public_payload->>'result_ref' END,
     'object_digest',CASE WHEN e.event_type='RunCreated' THEN e.internal_payload->'semantic_payload'->>'input_digest'
                         ELSE substring(e.public_payload->>'result_ref' FROM '([0-9a-f]{64})\\.json$') END
   ) AS value FROM public.execution_events e JOIN public.execution_events o
   ON o.stream_type=e.stream_type AND o.stream_id=e.stream_id AND o.stream_version=1
   WHERE e.owner_scope_key=scope_value AND o.owner_scope_key=scope_value AND e.stream_type='run'
     AND e.position>after_value AND e.position<=through_value
     AND o.event_type='RunCreated' AND o.public_payload->>'source_entity_type'='session'
     AND o.public_payload->>'source_entity_id'=collection_value
     AND e.event_type IN ('RunCreated','RunCompleted')
   ORDER BY e.position LIMIT limit_value
 ) page;
 RETURN result_value;
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
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(
        sa.text("""
CREATE TABLE execution_context_cuts (
 scope_key varchar(261) NOT NULL,request_id uuid NOT NULL,body jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(scope_key,request_id),
 CHECK(body->>'scope_key'=scope_key AND (body->>'format_version')::integer=1)
);
ALTER TABLE execution_context_cuts ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_context_cuts FORCE ROW LEVEL SECURITY;
""")
    )
    # The API can call narrow functions, but cannot read or mutate this private
    # receipt table or raw events. The owner policy applies signed current scope.
    for signature, ddl in FUNCTIONS.items():
        bind.execute(sa.text(ddl))
        bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        if "scope_allowed" not in signature:
            bind.execute(
                sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quote(api)},{quote(kernel)}")
            )
    bind.execute(
        sa.text(f"""
CREATE POLICY context_cut_owner ON execution_context_cuts FOR ALL TO {quote(owner)}
 USING (public.opencitadel_context_scope_allowed(scope_key))
 WITH CHECK (public.opencitadel_context_scope_allowed(scope_key))
""")
    )
    bind.execute(
        sa.text("""
CREATE FUNCTION public.opencitadel_context_cut_immutable() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'context_cut_immutable'; END $$;
CREATE TRIGGER context_cut_immutable BEFORE UPDATE OR DELETE ON execution_context_cuts
FOR EACH ROW EXECUTE FUNCTION public.opencitadel_context_cut_immutable();
""")
    )


def downgrade():
    op.execute("DROP TABLE execution_context_cuts")
    op.execute("DROP FUNCTION public.opencitadel_context_cut_immutable()")
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
