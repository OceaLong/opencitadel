"""Fixed native analysis Run pages and current-visible comparison metadata.

No backfill: captures accepted before this migration explicitly lack scalar facts.
"""

import sqlalchemy as sa

from alembic import op

revision = "0021analysis_native_reads"
down_revision = "0020execution_exports"
branch_labels = None
depends_on = None

CAPTURE = r"""
CREATE FUNCTION public.opencitadel_analysis_native_header() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 INSERT INTO public.analysis_native_captures(capture_id,scope_key) VALUES(NEW.id,NEW.scope_key);
 RETURN NEW;
END $$;
CREATE FUNCTION public.opencitadel_analysis_native_members() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM inserted_members m LEFT JOIN public.execution_view_runs r ON r.scope_key=m.scope_key AND r.run_id=m.run_id
 WHERE r.run_id IS NULL OR r.formal_position<>m.formal_position OR r.progress_position<>m.progress_position OR r.observed_order<>m.observed_order OR r.projection_revision<>m.projection_revision)
 THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
 INSERT INTO public.analysis_native_runs(capture_id,scope_key,run_id,ordinal,body)
 SELECT m.capture_id,m.scope_key,m.run_id,m.ordinal,jsonb_build_object('run_id',r.run_id,'family',r.family,'purpose',r.purpose,'execution_mode',r.execution_mode,'status',r.status,'admitted_at',r.admitted_at,'terminal_at',r.terminal_at,'admission_configuration_id',c.id)
 FROM inserted_members m JOIN public.execution_view_runs r ON r.scope_key=m.scope_key AND r.run_id=m.run_id
 LEFT JOIN LATERAL(SELECT id FROM public.execution_configurations c WHERE c.scope_key=m.scope_key AND c.run_id=m.run_id AND c.body->>'stage'='admission' ORDER BY c.created_at,c.id LIMIT 1)c ON true;
 RETURN NULL;
END $$;
"""
READ = r"""
CREATE FUNCTION public.opencitadel_analysis_native_page(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;capture uuid;after_ordinal integer;page_limit integer;checked jsonb;rows jsonb;
BEGIN
 p:=encoded::jsonb;s:=p->>'scope';capture:=(p->>'capture_id')::uuid;
 after_ordinal:=(p->>'after')::integer;page_limit:=(p->>'limit')::integer;
 IF p->>'operation' IS DISTINCT FROM 'read' OR after_ordinal IS NULL OR after_ordinal < -1 OR after_ordinal>=100000 OR page_limit IS NULL OR page_limit NOT BETWEEN 1 AND 200
 THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 -- Includes caller/expiry, full primary + accounting dependency manifest and seal checks.
 checked:=public.opencitadel_analysis_capture(encoded,signature);
 IF NOT EXISTS(SELECT 1 FROM public.analysis_native_captures c WHERE c.capture_id=capture AND c.scope_key=s)
 THEN RETURN jsonb_build_object('availability','retained_data_unavailable','items','[]'::jsonb); END IF;
 SELECT COALESCE(jsonb_agg(r.body||jsonb_build_object('ordinal',r.ordinal) ORDER BY r.ordinal),'[]'::jsonb) INTO rows
 FROM(SELECT body,ordinal FROM public.analysis_native_runs WHERE capture_id=capture AND scope_key=s AND ordinal>after_ordinal ORDER BY ordinal LIMIT page_limit+1)r;
 RETURN jsonb_build_object('availability','available','items',rows);
END $$;
CREATE FUNCTION public.opencitadel_comparison_native_context(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;saved record;visible uuid[];rows jsonb;details jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';
 IF p->>'operation' IS DISTINCT FROM 'native_context' OR jsonb_typeof(p->'run_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'run_ids')>201 THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT * INTO saved FROM public.comparison_revisions c WHERE c.scope_key=s AND c.id=(p->>'capture_id')::uuid AND c.published;
 IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 SELECT COALESCE(array_agg(run_id) FILTER(WHERE available),ARRAY[]::uuid[]) INTO visible FROM public.opencitadel_comparison_current_rows(s,saved.id);
 SELECT COALESCE(jsonb_agg(m.run_fact ORDER BY m.ordinal),'[]'::jsonb) INTO rows FROM public.comparison_members m
 WHERE m.scope_key=s AND m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.run_id IN(SELECT value::uuid FROM jsonb_array_elements_text(p->'run_ids'));
 SELECT COALESCE(jsonb_agg(d.run_id ORDER BY d.slot),'[]'::jsonb) INTO details FROM public.comparison_details d WHERE d.scope_key=s AND d.capture_id=saved.id AND d.run_id=ANY(visible);
 -- Exclusions and original counts/IDs are intentionally not a public reload inventory.
 RETURN jsonb_build_object('query',saved.query,'selection_mode',saved.selection->>'mode','detail_run_ids',details,'members',rows);
END $$;
"""


BODY_CONTEXT = r"""
CREATE FUNCTION public.opencitadel_comparison_content_context(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;saved record;member record;step jsonb;ref jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';
 IF p->>'operation' IS DISTINCT FROM 'content_context' OR p->>'kind' NOT IN ('input','output') THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT * INTO saved FROM public.comparison_revisions WHERE scope_key=s AND comparison_id=(p->>'comparison_id')::uuid AND revision=(p->>'revision')::integer AND published;
 IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.opencitadel_comparison_current_rows(s,saved.id) c WHERE c.run_id=(p->>'run_id')::uuid AND c.available)
 THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
 SELECT * INTO member FROM public.comparison_members WHERE scope_key=s AND capture_id=saved.id AND run_id=(p->>'run_id')::uuid;
 SELECT value INTO step FROM public.comparison_details d CROSS JOIN LATERAL jsonb_array_elements(d.body->'steps') e(value)
 WHERE d.scope_key=s AND d.capture_id=saved.id AND d.run_id=member.run_id AND e.value->>'step_id'=p->>'step_id';
 IF step IS NULL THEN RETURN NULL; END IF;
 ref:=step->((p->>'kind')||'_ref');
 IF ref IS NULL OR ref->>'availability' IS DISTINCT FROM 'available' THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('content_id',ref->>'content_id','formal_position',member.formal_position);
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
    definitions = {
        "analysis_native_captures": "capture_id uuid PRIMARY KEY REFERENCES public.analysis_captures(id) ON DELETE CASCADE,scope_key text NOT NULL",
        "analysis_native_runs": "capture_id uuid NOT NULL REFERENCES public.analysis_native_captures(capture_id) ON DELETE CASCADE,scope_key text NOT NULL,run_id uuid NOT NULL,ordinal integer NOT NULL,body jsonb NOT NULL,PRIMARY KEY(capture_id,run_id),UNIQUE(capture_id,ordinal)",
    }
    for table, ddl in definitions.items():
        bind.execute(sa.text(f"CREATE TABLE public.{table}({ddl})"))
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        bind.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        condition = "public.opencitadel_authorization_valid() AND ((current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END) OR (current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'))"
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_native_owner ON public.{table} TO {quote(owner)} USING({condition}) WITH CHECK({condition})"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE TRIGGER analysis_native_immutable BEFORE UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_capture_immutable()"
            )
        )
    for sql in (CAPTURE, READ, BODY_CONTEXT):
        bind.execute(sa.text(sql))
    for name, args in (
        ("analysis_native_header", ""),
        ("analysis_native_members", ""),
        ("analysis_native_page", "text,text"),
        ("comparison_native_context", "text,text"),
        ("comparison_content_context", "text,text"),
    ):
        bind.execute(
            sa.text(
                f"REVOKE ALL ON FUNCTION public.opencitadel_{name}({args}) FROM PUBLIC,{quote(api)},{quote(kernel)}"
            )
        )
    for name in ("analysis_native_page", "comparison_native_context", "comparison_content_context"):
        bind.execute(
            sa.text(
                f"GRANT EXECUTE ON FUNCTION public.opencitadel_{name}(text,text) TO {quote(api)},{quote(kernel)}"
            )
        )
    bind.execute(
        sa.text(
            "CREATE TRIGGER analysis_native_header AFTER INSERT ON public.analysis_captures FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_native_header()"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER analysis_native_members AFTER INSERT ON public.analysis_capture_members REFERENCING NEW TABLE AS inserted_members FOR EACH STATEMENT EXECUTE FUNCTION public.opencitadel_analysis_native_members()"
        )
    )


def downgrade():
    op.execute("DROP TRIGGER analysis_native_members ON public.analysis_capture_members")
    op.execute("DROP TRIGGER analysis_native_header ON public.analysis_captures")
    for name, args in (
        ("analysis_native_header", ""),
        ("analysis_native_members", ""),
        ("analysis_native_page", "text,text"),
        ("comparison_native_context", "text,text"),
        ("comparison_content_context", "text,text"),
    ):
        op.execute(f"DROP FUNCTION public.opencitadel_{name}({args})")
    op.execute("DROP TABLE public.analysis_native_runs")
    op.execute("DROP TABLE public.analysis_native_captures")
