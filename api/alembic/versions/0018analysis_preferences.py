"""Optional scoped analysis timezone and caller-private transactional receipts."""

import sqlalchemy as sa

from alembic import op

revision = "0018analysis_preferences"
down_revision = "0017execution_comparisons"
branch_labels = None
depends_on = None

PREFERENCE = r"""
CREATE FUNCTION public.opencitadel_analysis_preference(encoded text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; s text; actor text; team text; saved jsonb; answer jsonb; fingerprint text; old_fingerprint text; current_revision bigint; zone text;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb; s:=p->>'scope'; actor:=p->'principal'->>'user_id';
 team:=NULLIF(current_setting('app.team_id',true),'');
 IF p->>'operation'='get' THEN
   SELECT jsonb_build_object('timezone',timezone,'revision',revision) INTO answer FROM public.analysis_preferences WHERE scope_key=s;
   RETURN COALESCE(answer,jsonb_build_object('timezone',NULL,'revision',0));
 END IF;
 IF p->>'operation' IS DISTINCT FROM 'update' OR jsonb_typeof(p->'expected_revision') IS DISTINCT FROM 'number'
 OR (p->>'expected_revision') !~ '^[0-9]+$' OR length(p->>'request_id') NOT BETWEEN 1 AND 128
 OR p->>'request_id' IS NULL OR NOT(p ? 'timezone') THEN RAISE EXCEPTION 'invalid_analysis_preference'; END IF;
 IF p->'principal'->>'global_role'='auditor' OR (team IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM public.team_members WHERE team_id=team AND user_id=actor AND role IN ('owner','admin')))
 THEN RAISE EXCEPTION 'preference_permission_denied'; END IF;
 zone:=p->>'timezone';
 IF zone IS NOT NULL AND (length(zone)>255 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=zone))
 THEN RAISE EXCEPTION 'invalid_analysis_preference'; END IF;
 -- Scope lock serializes initial absent rows, CAS and receipts across API processes.
 PERFORM pg_advisory_xact_lock(hashtextextended('analysis-preference:'||s,0));
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 fingerprint:=encode(public.digest(convert_to(jsonb_build_object('timezone',zone,'expected_revision',p->'expected_revision')::text,'UTF8'),'sha256'),'hex');
 SELECT body,request_fingerprint INTO answer,old_fingerprint FROM public.analysis_preference_receipts
 WHERE scope_key=s AND caller_id=actor AND request_id=p->>'request_id';
 IF FOUND THEN
   IF old_fingerprint<>fingerprint THEN RAISE EXCEPTION 'preference_request_conflict'; END IF;
   RETURN answer;
 END IF;
 SELECT revision INTO current_revision FROM public.analysis_preferences WHERE scope_key=s;
 IF COALESCE(current_revision,0)<>(p->>'expected_revision')::bigint THEN RAISE EXCEPTION 'preference_conflict'; END IF;
 INSERT INTO public.analysis_preferences(scope_key,user_id,team_id,timezone,revision)
 VALUES(s,CASE WHEN team IS NULL THEN actor ELSE NULL END,team,zone,COALESCE(current_revision,0)+1)
 ON CONFLICT(scope_key) DO UPDATE SET timezone=EXCLUDED.timezone,revision=EXCLUDED.revision;
 answer:=jsonb_build_object('timezone',zone,'revision',COALESCE(current_revision,0)+1);
 INSERT INTO public.analysis_preference_receipts(scope_key,caller_id,request_id,request_fingerprint,body)
 VALUES(s,actor,p->>'request_id',fingerprint,answer);
 RETURN answer;
END $$
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
    bind.execute(
        sa.text("""CREATE TABLE public.analysis_preferences(
      scope_key text PRIMARY KEY,user_id varchar REFERENCES public.users(id) ON DELETE CASCADE,
      team_id varchar REFERENCES public.teams(id) ON DELETE CASCADE,timezone text,revision bigint NOT NULL CHECK(revision>=1),
      CHECK((user_id IS NOT NULL AND team_id IS NULL AND scope_key='user:'||user_id) OR
            (user_id IS NULL AND team_id IS NOT NULL AND scope_key='team:'||team_id)))""")
    )
    bind.execute(
        sa.text("""CREATE TABLE public.analysis_preference_receipts(
      scope_key text NOT NULL REFERENCES public.analysis_preferences(scope_key) ON DELETE CASCADE,
      caller_id varchar NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,request_id text NOT NULL CHECK(length(request_id) BETWEEN 1 AND 128),
      request_fingerprint text NOT NULL,body jsonb NOT NULL,PRIMARY KEY(scope_key,caller_id,request_id))""")
    )
    for table in ("analysis_preferences", "analysis_preference_receipts"):
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        bind.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        caller = (
            " AND caller_id=current_setting('app.user_id',true)"
            if table.endswith("receipts")
            else ""
        )
        condition = (
            "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
            + caller
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_preference_owner ON public.{table} TO {quote(owner)} USING({condition}) WITH CHECK({condition})"
            )
        )
    bind.execute(sa.text(PREFERENCE))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_preference(text,text) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_preference(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )


def downgrade():
    op.execute("DROP FUNCTION public.opencitadel_analysis_preference(text,text)")
    op.execute("DROP TABLE public.analysis_preference_receipts")
    op.execute("DROP TABLE public.analysis_preferences")
