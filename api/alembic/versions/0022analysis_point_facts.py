"""Retain independently gated E11 case-result total-cost contributors and series rows."""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0022analysis_point_facts"
down_revision = "0021analysis_native_reads"
branch_labels = None
depends_on = None

FUNCTION = r"""
CREATE FUNCTION public.opencitadel_analysis_points(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;kind text;capture uuid;actor text;resources jsonb;records jsonb;members jsonb;checked jsonb;stored text;current_stamp text;visible uuid[];result jsonb;n integer;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';kind:=p->>'capture_kind';capture:=(p->>'capture_id')::uuid;actor:=current_setting('app.user_id',true);
 IF kind NOT IN ('analysis','comparison') THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 IF kind='analysis' THEN
  IF NOT EXISTS(SELECT 1 FROM public.analysis_captures c WHERE c.id=capture AND c.scope_key=s AND c.caller_id=actor AND c.expires_at>clock_timestamp()) THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s AND (c.published OR c.caller_id=actor)) THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 END IF;
 IF p->>'operation'='prepare' THEN
  IF (kind='analysis' AND EXISTS(SELECT 1 FROM public.analysis_capture_metrics WHERE capture_id=capture)) OR (kind='comparison' AND EXISTS(SELECT 1 FROM public.comparison_revisions WHERE id=capture AND published))
  THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  IF kind='analysis' THEN
   records:=public.opencitadel_analysis_scores(encoded,signature,capture);
   SELECT COALESCE(jsonb_agg(q.body||jsonb_build_object('run_id',a.run_id)),'[]'::jsonb) INTO records FROM jsonb_array_elements(records) q(body)
   JOIN public.evaluation_batch_results r ON r.scope_key=s AND r.id=(q.body->>'result_id')::uuid
   JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.result_id=r.id AND a.attempt=r.attempt;
  ELSE SELECT COALESCE(jsonb_agg(body),'[]'::jsonb) INTO records FROM public.comparison_scores WHERE scope_key=s AND capture_id=capture; END IF;
  IF jsonb_array_length(records)>100000 THEN RAISE EXCEPTION 'analysis_capacity_exceeded'; END IF;
  INSERT INTO public.analysis_point_captures(capture_kind,capture_id,scope_key,analysis_id,comparison_id)
  VALUES(kind,capture,s,CASE WHEN kind='analysis' THEN capture END,CASE WHEN kind='comparison' THEN capture END);
  WITH results AS(SELECT DISTINCT (x->>'result_id')::uuid AS id FROM jsonb_array_elements(records)x),
  links AS(SELECT a.result_id,a.run_id FROM results r JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.result_id=r.id
   UNION SELECT j.result_id,j.run_id FROM results r JOIN public.evaluation_judge_intents j ON j.scope_key=s AND j.result_id=r.id WHERE j.run_id IS NOT NULL),
  bounded AS(SELECT * FROM links LIMIT 1000001)
  INSERT INTO public.analysis_point_dependencies(capture_kind,capture_id,scope_key,result_id,run_id,cut)
  SELECT kind,capture,s,l.result_id,l.run_id,jsonb_build_object('run_id',l.run_id,'formal_position',v.formal_position,'progress_position',v.progress_position,'observed_order',v.observed_order,'projector_version',v.projector_version,'generation',COALESCE((SELECT c.active_generation::text FROM public.execution_view_controls c WHERE c.scope_key=s),'live'))
  FROM bounded l LEFT JOIN public.execution_view_runs v ON v.scope_key=s AND v.run_id=l.run_id;
  GET DIAGNOSTICS n=ROW_COUNT;
  IF n>1000000 THEN RAISE EXCEPTION 'analysis_accounting_capacity_exceeded'; END IF;
  SELECT COALESCE(jsonb_agg(cut),'[]'::jsonb) INTO members FROM(SELECT DISTINCT cut FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture AND scope_key=s)d;
  checked:=public.opencitadel_analysis_manifest(encoded,signature,members);
  IF kind='comparison' THEN
   resources:=public.opencitadel_analysis_judge_resources(encoded,signature,members);
   INSERT INTO public.comparison_resources(capture_id,scope_key,run_id,source,resources,pins,owners)
   SELECT capture,s,r.run_id,r.source,r.resources,r.pins,r.owners FROM public.opencitadel_analysis_point_bindings(s,capture,members,resources)r ON CONFLICT(capture_id,run_id) DO NOTHING;
   SELECT jsonb_build_object('rows',COALESCE(jsonb_agg(jsonb_build_object('run_id',run_id,'available',available,'manifest',available::text) ORDER BY run_id),'[]'::jsonb),'manifest',md5(COALESCE(string_agg(run_id::text||':'||available::text,'|' ORDER BY run_id),''))) INTO checked
   FROM public.opencitadel_comparison_current_rows(s,capture,ARRAY(SELECT DISTINCT run_id FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture));
  END IF;
  UPDATE public.analysis_point_dependencies d SET manifest=x->>'manifest',captured_available=COALESCE((x->>'available')::boolean,false)  FROM jsonb_array_elements(checked->'rows')x
  WHERE d.capture_kind=kind AND d.capture_id=capture AND d.scope_key=s AND d.run_id=(x->>'run_id')::uuid;
  -- Initially absent sources remain absent at this cut; later dispatch is not revocation.
  SELECT COALESCE(jsonb_agg(cut),'[]'::jsonb) INTO members FROM(SELECT DISTINCT cut FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture AND scope_key=s AND captured_available)d;
  IF kind='comparison' THEN
   SELECT jsonb_build_object('manifest',md5(COALESCE(string_agg(run_id::text||':'||available::text,'|' ORDER BY run_id),''))) INTO checked
   FROM public.opencitadel_comparison_current_rows(s,capture,ARRAY(SELECT DISTINCT run_id FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture AND captured_available));
  ELSE checked:=public.opencitadel_analysis_manifest(encoded,signature,members); END IF;
  UPDATE public.analysis_point_captures SET fingerprint=checked->>'manifest' WHERE capture_kind=kind AND capture_id=capture AND scope_key=s;
  RETURN jsonb_build_object('records',records,'fingerprint',checked->>'manifest','resources',CASE WHEN kind='comparison' THEN (SELECT COALESCE(jsonb_agg(DISTINCT resource),'[]'::jsonb) FROM public.comparison_resources r CROSS JOIN LATERAL jsonb_array_elements(r.resources)resource WHERE r.capture_id=capture AND r.scope_key=s AND EXISTS(SELECT 1 FROM public.analysis_point_dependencies d WHERE d.capture_kind=kind AND d.capture_id=capture AND d.run_id=r.run_id AND d.captured_available)) ELSE '[]'::jsonb END);
 ELSIF p->>'operation'='store' THEN
  IF (kind='analysis' AND EXISTS(SELECT 1 FROM public.analysis_capture_metrics WHERE capture_id=capture)) OR (kind='comparison' AND EXISTS(SELECT 1 FROM public.comparison_revisions WHERE id=capture AND published))
  THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  IF jsonb_typeof(p->'records') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'records')+(SELECT count(*) FROM public.analysis_point_rows WHERE capture_kind=kind AND capture_id=capture)>100000 OR octet_length((p->'records')::text)>134217728 THEN RAISE EXCEPTION 'analysis_capacity_exceeded'; END IF;
  INSERT INTO public.analysis_point_rows(capture_kind,capture_id,scope_key,run_id,result_id,body)
  SELECT kind,capture,s,(x->>'primary_run_id')::uuid,(x->'row'->>'id')::uuid,x FROM jsonb_array_elements(p->'records')x;
  RETURN '{}'::jsonb;
 ELSIF p->>'operation' NOT IN ('read','current') THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 SELECT fingerprint INTO stored FROM public.analysis_point_captures WHERE capture_kind=kind AND capture_id=capture AND scope_key=s;
 IF NOT FOUND THEN RETURN jsonb_build_object('records',NULL,'fingerprint','','captured_fingerprint',''); END IF;
 SELECT COALESCE(jsonb_agg(cut),'[]'::jsonb) INTO members FROM(SELECT DISTINCT cut FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture AND scope_key=s AND captured_available)d;
 IF kind='comparison' THEN
  SELECT jsonb_build_object('rows',COALESCE(jsonb_agg(jsonb_build_object('run_id',c.run_id,'available',c.available AND pins.available,'manifest',(c.available AND pins.available)::text) ORDER BY c.run_id),'[]'::jsonb),'manifest',md5(COALESCE(string_agg(c.run_id::text||':'||(c.available AND pins.available)::text,'|' ORDER BY c.run_id),''))) INTO checked
  FROM public.opencitadel_comparison_current_rows(s,capture,ARRAY(SELECT DISTINCT run_id FROM public.analysis_point_dependencies WHERE capture_kind=kind AND capture_id=capture AND captured_available))c
  CROSS JOIN LATERAL(SELECT NOT EXISTS(SELECT 1 FROM public.comparison_resources r CROSS JOIN LATERAL jsonb_array_elements(r.resources)x WHERE r.capture_id=capture AND r.scope_key=s AND r.run_id=c.run_id AND NOT EXISTS(SELECT 1 FROM public.resource_pins pin WHERE pin.scope_key=s AND pin.owner_kind='comparison_revision' AND pin.owner_id=capture::text AND pin.resource_kind=x->>'resource_kind' AND pin.resource_id=x->>'resource_id' AND pin.resource_version=x->>'resource_version' AND pin.available)) AS available)pins;
 ELSE checked:=public.opencitadel_analysis_manifest(encoded,signature,members); END IF;
 current_stamp:=checked->>'manifest';
 IF p->>'operation'='current' THEN RETURN jsonb_build_object('fingerprint',current_stamp,'captured_fingerprint',stored); END IF;
 IF kind='comparison' THEN SELECT COALESCE(array_agg(run_id) FILTER(WHERE available),ARRAY[]::uuid[]) INTO visible FROM public.opencitadel_comparison_current_rows(s,capture);
 ELSE SELECT COALESCE(array_agg(run_id),ARRAY[]::uuid[]) INTO visible FROM public.analysis_capture_members WHERE capture_id=capture AND scope_key=s; END IF;
 SELECT COALESCE(jsonb_agg(CASE WHEN EXISTS(
   SELECT 1 FROM public.analysis_point_dependencies d LEFT JOIN LATERAL(SELECT x FROM jsonb_array_elements(checked->'rows')x WHERE x->>'run_id'=d.run_id::text)c ON true
   WHERE d.capture_kind=kind AND d.capture_id=capture AND d.scope_key=s AND d.result_id=r.result_id AND (NOT d.captured_available OR (c.x->>'available')::boolean IS DISTINCT FROM true OR c.x->>'manifest' IS DISTINCT FROM d.manifest))
  THEN jsonb_set(jsonb_set(r.body,'{row,subject_usage}','null'::jsonb),'{row,judge_usage}','null'::jsonb) ELSE r.body END ORDER BY r.id),'[]'::jsonb) INTO result
 FROM public.analysis_point_rows r WHERE r.capture_kind=kind AND r.capture_id=capture AND r.scope_key=s AND r.run_id=ANY(visible);
 RETURN jsonb_build_object('records',result,'fingerprint',current_stamp,'captured_fingerprint',stored);
END $$;
"""


RESOLVE = r"""
CREATE FUNCTION public.opencitadel_comparison_native_resolve(encoded text,signature text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;answer uuid;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;
 IF p->>'operation' IS DISTINCT FROM 'resolve' THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT id INTO answer FROM public.comparison_revisions WHERE scope_key=p->>'scope' AND comparison_id=(p->>'comparison_id')::uuid AND revision=(p->>'revision')::integer AND published;
 IF answer IS NULL THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 RETURN answer;
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
    defs = {
        "analysis_point_captures": "capture_kind text NOT NULL,capture_id uuid NOT NULL,scope_key text NOT NULL,analysis_id uuid REFERENCES public.analysis_captures(id) ON DELETE CASCADE,comparison_id uuid REFERENCES public.comparison_revisions(id),fingerprint text,PRIMARY KEY(capture_kind,capture_id),CHECK((capture_kind='analysis' AND analysis_id=capture_id AND comparison_id IS NULL) OR (capture_kind='comparison' AND comparison_id=capture_id AND analysis_id IS NULL))",
        "analysis_point_dependencies": "capture_kind text NOT NULL,capture_id uuid NOT NULL,scope_key text NOT NULL,result_id uuid NOT NULL,run_id uuid NOT NULL,cut jsonb NOT NULL,manifest text,captured_available boolean NOT NULL DEFAULT false,PRIMARY KEY(capture_kind,capture_id,result_id,run_id),FOREIGN KEY(capture_kind,capture_id) REFERENCES public.analysis_point_captures(capture_kind,capture_id) ON DELETE CASCADE",
        "analysis_point_rows": "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,capture_kind text NOT NULL,capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,result_id uuid NOT NULL,body jsonb NOT NULL,FOREIGN KEY(capture_kind,capture_id) REFERENCES public.analysis_point_captures(capture_kind,capture_id) ON DELETE CASCADE",
    }
    for table, ddl in defs.items():
        bind.execute(sa.text(f"CREATE TABLE public.{table}({ddl})"))
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        bind.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        condition = "public.opencitadel_authorization_valid() AND ((current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END) OR (current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'))"
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_points_owner ON public.{table} TO {quote(owner)} USING({condition}) WITH CHECK({condition})"
            )
        )
    bind.execute(
        sa.text(
            "CREATE INDEX analysis_point_rows_capture ON public.analysis_point_rows(capture_kind,capture_id,run_id)"
        )
    )
    historical = runpy.run_path(str(Path(__file__).with_name("0017execution_comparisons.py")))[
        "RESOURCE_BINDINGS"
    ]
    binding_sql = (
        "CREATE FUNCTION public.opencitadel_analysis_point_bindings(scope_value text,capture_id_value uuid,members_json jsonb,resources_json jsonb) RETURNS TABLE(run_id uuid,source jsonb,resources jsonb,pins jsonb,owners jsonb) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE owner_value text;team_value text; BEGIN team_value:=NULLIF(current_setting('app.team_id',true),'');owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END; RETURN QUERY "
        + historical
        + "; END $$"
    )
    bind.execute(sa.text(binding_sql))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb) FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(sa.text(FUNCTION))
    bind.execute(sa.text(RESOLVE))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_comparison_native_resolve(text,text) FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_comparison_native_resolve(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_analysis_points(text,text) FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_points(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )


def downgrade():
    op.execute("DROP FUNCTION public.opencitadel_analysis_point_bindings(text,uuid,jsonb,jsonb)")
    op.execute("DROP FUNCTION public.opencitadel_comparison_native_resolve(text,text)")
    op.execute("DROP FUNCTION public.opencitadel_analysis_points(text,text)")
    for table in ("analysis_point_rows", "analysis_point_dependencies", "analysis_point_captures"):
        op.execute(f"DROP TABLE public.{table}")
