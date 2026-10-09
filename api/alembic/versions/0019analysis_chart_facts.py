"""Retain per-run tool counts and read exact fixed latency boundaries for charts.

Previous comparison revisions remain untouched. New member insertion captures tool
facts set-wise in the same transaction; no historical capture is backfilled.
"""

import sqlalchemy as sa

from alembic import op

revision = "0019analysis_chart_facts"
down_revision = "0018analysis_preferences"
branch_labels = None
depends_on = None

TOOL_ROWS = r"""
CREATE FUNCTION public.opencitadel_analysis_tool_rows(s text, selected uuid[]) RETURNS TABLE(run_id uuid,tool_name text,terminal bigint,errors bigint,execution_errors bigint,business_errors bigint,excluded bigint,unknown bigint,deferred bigint,cancelled bigint)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT t.run_id,t.tool_name::text,
 count(*) FILTER(WHERE t.status IN ('completed','failed')),
 count(*) FILTER(WHERE t.status IN ('completed','failed') AND (t.status='failed' OR t.business_outcome IN ('failure','failed'))),
 count(*) FILTER(WHERE t.status='failed'),
 count(*) FILTER(WHERE t.status IN ('completed','failed') AND t.business_outcome IN ('failure','failed')),
 count(*) FILTER(WHERE t.status NOT IN ('completed','failed')),
 count(*) FILTER(WHERE t.status='unknown'),count(*) FILTER(WHERE t.status='deferred'),count(*) FILTER(WHERE t.status='cancelled')
 FROM unnest(selected) member(run_id) JOIN public.execution_view_steps t ON t.scope_key=s AND t.run_id=member.run_id
 WHERE t.kind='tool' AND t.attempt_id IS NOT NULL AND t.status<>'queued'
 GROUP BY t.run_id,t.tool_name
$$
"""

CAPTURE = r"""
CREATE FUNCTION public.opencitadel_comparison_capture_tools() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capture record;
BEGIN
 FOR capture IN SELECT scope_key,capture_id,array_agg(run_id) AS members FROM inserted_members GROUP BY scope_key,capture_id LOOP
  IF NOT EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=capture.capture_id AND c.scope_key=capture.scope_key AND NOT c.published)
  THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
  INSERT INTO public.comparison_tool_captures(capture_id,scope_key) VALUES(capture.capture_id,capture.scope_key) ON CONFLICT DO NOTHING;
  INSERT INTO public.comparison_tool_facts(capture_id,scope_key,run_id,tool_name,body)
  SELECT capture.capture_id,capture.scope_key,t.run_id,t.tool_name,to_jsonb(t)-'run_id'-'tool_name'
  FROM public.opencitadel_analysis_tool_rows(capture.scope_key,capture.members) t;
 END LOOP;
 RETURN NULL;
END $$
"""

READ = r"""
CREATE FUNCTION public.opencitadel_analysis_chart_facts(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;capture uuid;selected uuid[];runs jsonb;tools jsonb; saved record;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';capture:=(p->>'capture_id')::uuid;
 IF p->>'operation'='live' THEN
  SELECT * INTO saved FROM public.analysis_captures c WHERE c.id=capture AND c.scope_key=s AND c.caller_id=current_setting('app.user_id',true) AND c.expires_at>clock_timestamp();
  IF NOT FOUND OR EXISTS(SELECT 1 FROM public.analysis_capture_metrics WHERE capture_id=capture)
  THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  -- Live facts may only supplement an unsealed capture at its exact execution cut.
  IF EXISTS(SELECT 1 FROM public.analysis_capture_members m LEFT JOIN public.execution_view_runs r ON r.scope_key=s AND r.run_id=m.run_id
   WHERE m.capture_id=capture AND m.scope_key=s AND (r.run_id IS NULL OR r.formal_position<>m.formal_position OR r.progress_position<>m.progress_position OR r.observed_order<>m.observed_order OR r.projection_revision<>m.projection_revision))
  THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  SELECT COALESCE(array_agg(run_id),ARRAY[]::uuid[]) INTO selected FROM public.analysis_capture_members WHERE scope_key=s AND capture_id=capture;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('run_id',r.run_id,'family',r.family,'purpose',r.purpose,'execution_mode',r.execution_mode,
    'configuration_revision',CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(saved.query->'filters') f WHERE f->>0='configuration_revision') THEN c.id ELSE NULL END,
    'status',r.status,'duration_ms',CASE WHEN r.status IN ('completed','failed') AND r.terminal_at>=r.admitted_at THEN extract(epoch FROM r.terminal_at-r.admitted_at)*1000 ELSE NULL END) ORDER BY r.run_id),'[]'::jsonb) INTO runs
  FROM unnest(selected) m(run_id) JOIN public.execution_view_runs r ON r.scope_key=s AND r.run_id=m.run_id
  LEFT JOIN LATERAL(SELECT id FROM public.execution_configurations c WHERE c.scope_key=s AND c.run_id=r.run_id AND c.body->>'stage'='admission' ORDER BY c.created_at,c.id LIMIT 1)c ON true;
  SELECT COALESCE(jsonb_agg(to_jsonb(t)),'[]'::jsonb) INTO tools FROM public.opencitadel_analysis_tool_rows(s,selected) t;
 ELSIF p->>'operation'='comparison' THEN
  SELECT * INTO saved FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s AND c.published;
  IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
  SELECT COALESCE(array_agg(run_id) FILTER(WHERE available),ARRAY[]::uuid[]) INTO selected FROM public.opencitadel_comparison_current_rows(s,capture);
  SELECT COALESCE(jsonb_agg(jsonb_build_object('run_id',m.run_id,'family',m.run_fact->'family','purpose',m.run_fact->'purpose','execution_mode',m.run_fact->'execution_mode',
   'configuration_revision',m.run_fact->'configuration_revision','status',m.run_fact->'status',
   'duration_ms',CASE WHEN m.run_fact->>'status' IN ('completed','failed') AND (m.run_fact->>'terminal_at')::timestamptz>=(m.run_fact->>'admitted_at')::timestamptz THEN extract(epoch FROM (m.run_fact->>'terminal_at')::timestamptz-(m.run_fact->>'admitted_at')::timestamptz)*1000 ELSE NULL END) ORDER BY m.run_id),'[]'::jsonb) INTO runs
  FROM public.comparison_members m WHERE m.capture_id=capture AND m.scope_key=s AND m.run_id=ANY(selected);
  IF EXISTS(SELECT 1 FROM public.comparison_tool_captures c WHERE c.capture_id=capture AND c.scope_key=s) THEN
   SELECT COALESCE(jsonb_agg(t.body||jsonb_build_object('tool_name',t.tool_name)),'[]'::jsonb) INTO tools
   FROM public.comparison_tool_facts t WHERE t.capture_id=capture AND t.scope_key=s AND t.run_id=ANY(selected);
  END IF;
 ELSE RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 -- The wire facts are internal. Aggregate the retained per-run counters, never a latest step join.
 IF tools IS NOT NULL THEN
  SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.tool_name),'[]'::jsonb) INTO tools FROM(
   SELECT x->>'tool_name' AS tool_name,sum((x->>'terminal')::bigint) AS terminal,sum((x->>'errors')::bigint) AS errors,
   sum((x->>'execution_errors')::bigint) AS execution_errors,sum((x->>'business_errors')::bigint) AS business_errors,
   sum((x->>'excluded')::bigint) AS excluded,sum((x->>'unknown')::bigint) AS unknown,sum((x->>'deferred')::bigint) AS deferred,sum((x->>'cancelled')::bigint) AS cancelled
   FROM jsonb_array_elements(tools) x GROUP BY x->>'tool_name')t;
 END IF;
 RETURN jsonb_build_object('runs',runs,'tools',tools);
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
        sa.text(
            "CREATE TABLE public.comparison_tool_captures(capture_id uuid PRIMARY KEY REFERENCES public.comparison_revisions(id),scope_key text NOT NULL)"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TABLE public.comparison_tool_facts(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,capture_id uuid NOT NULL REFERENCES public.comparison_tool_captures(capture_id),scope_key text NOT NULL,run_id uuid NOT NULL,tool_name text,body jsonb NOT NULL)"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX comparison_tool_facts_members ON public.comparison_tool_facts(capture_id,run_id)"
        )
    )
    for table in ("comparison_tool_captures", "comparison_tool_facts"):
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        bind.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        condition = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
        bind.execute(
            sa.text(
                f"CREATE POLICY comparison_chart_owner ON public.{table} TO {quote(owner)} USING({condition}) WITH CHECK({condition})"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE TRIGGER comparison_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_comparison_immutable()"
            )
        )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_comparison_chart_header() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      INSERT INTO public.comparison_tool_captures(capture_id,scope_key) VALUES(NEW.id,NEW.scope_key); RETURN NEW; END $$""")
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_comparison_chart_header() FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER comparison_chart_header AFTER INSERT ON public.comparison_revisions FOR EACH ROW EXECUTE FUNCTION public.opencitadel_comparison_chart_header()"
        )
    )
    for sql in (TOOL_ROWS, CAPTURE, READ):
        bind.execute(sa.text(sql))
    for name, signature in (
        ("analysis_tool_rows", "text,uuid[]"),
        ("comparison_capture_tools", ""),
        ("analysis_chart_facts", "text,text"),
    ):
        bind.execute(
            sa.text(
                f"REVOKE ALL ON FUNCTION public.opencitadel_{name}({signature}) FROM PUBLIC,{quote(api)},{quote(kernel)}"
            )
        )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_chart_facts(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER comparison_capture_tools AFTER INSERT ON public.comparison_members REFERENCING NEW TABLE AS inserted_members FOR EACH STATEMENT EXECUTE FUNCTION public.opencitadel_comparison_capture_tools()"
        )
    )


def downgrade():
    op.execute("DROP TRIGGER comparison_chart_header ON public.comparison_revisions")
    op.execute("DROP FUNCTION public.opencitadel_comparison_chart_header()")
    op.execute("DROP TRIGGER comparison_capture_tools ON public.comparison_members")
    op.execute("DROP FUNCTION public.opencitadel_comparison_capture_tools()")
    op.execute("DROP FUNCTION public.opencitadel_analysis_chart_facts(text,text)")
    op.execute("DROP FUNCTION public.opencitadel_analysis_tool_rows(text,uuid[])")
    op.execute("DROP TABLE public.comparison_tool_facts")
    op.execute("DROP TABLE public.comparison_tool_captures")
