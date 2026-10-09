"""Export-owned fixed captures and transaction-private comparison staging.

The source fact/read and resource predicates below are frozen from approved 0017.
No migration imports mutable application SQL or modifies a predecessor.
"""

import sqlalchemy as sa

from alembic import op

revision = "0020execution_exports"
down_revision = "0019analysis_chart_facts"
branch_labels = None
depends_on = None

SOURCE_FACTS = r"""
CREATE FUNCTION public.opencitadel_export_source_facts(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;epoch bigint;saved record;visible uuid[];accounting uuid[];section jsonb;facts jsonb;result jsonb;
 page_limit integer;after_ordinal integer;grain_value text;timezone_value text;accounting_mode text;selected_batch text;stamp text;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 IF p->>'operation' IS DISTINCT FROM 'read' THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT c.* INTO saved FROM public.comparison_revisions c WHERE c.scope_key=s AND c.comparison_id=(p->>'comparison_id')::uuid AND c.revision=(p->>'revision')::integer AND (c.published OR EXISTS(SELECT 1 FROM public.export_staging t WHERE t.capture_id=c.id AND t.transaction_id=pg_current_xact_id() AND t.caller_id=current_setting('app.user_id',true)));
 IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 page_limit:=COALESCE((p->>'limit')::integer,100);after_ordinal:=COALESCE((p->>'after')::integer,-1);
 IF page_limit NOT BETWEEN 1 AND 200 OR after_ordinal < -1 OR jsonb_typeof(p->'detail_run_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'detail_run_ids')>5 THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT COALESCE(array_agg(c.run_id) FILTER(WHERE c.available),ARRAY[]::uuid[]),md5(saved.alignment_revision::text||COALESCE(string_agg(c.run_id::text||':'||c.available::text,',' ORDER BY c.run_id),''))
 INTO visible,stamp FROM public.opencitadel_comparison_current_rows(s,saved.id) c;
 grain_value:=saved.query->>'grain';timezone_value:=saved.query->>'timezone';
 SELECT f->>1 INTO accounting_mode FROM jsonb_array_elements(saved.query->'filters') f WHERE f->>0='accounting';
 SELECT f->>1 INTO selected_batch FROM jsonb_array_elements(saved.query->'filters') f WHERE f->>0='batch_id';
 accounting_mode:=COALESCE(accounting_mode,'run');
 SELECT COALESCE(array_agg(a.run_id),ARRAY[]::uuid[]) INTO accounting FROM public.comparison_accounting a
 WHERE a.capture_id=saved.id AND a.run_id=ANY(visible) AND
 (accounting_mode='run' OR (accounting_mode='batch_total' AND EXISTS(SELECT 1 FROM public.comparison_scores q WHERE q.capture_id=saved.id AND q.run_id=ANY(visible) AND q.body->>'batch_id'=selected_batch))
 OR (accounting_mode='selected_result' AND EXISTS(SELECT 1 FROM public.comparison_accounting_links l JOIN public.comparison_scores q ON q.capture_id=l.capture_id AND q.body->>'result_id'=l.result_id::text WHERE l.capture_id=saved.id AND l.run_id=a.run_id AND q.run_id=ANY(visible))));
 facts:=jsonb_build_object('captured_at',saved.captured_at,'accounting_run_count',cardinality(accounting),
 'coverage',CASE WHEN EXISTS(SELECT 1 FROM public.comparison_members m WHERE m.capture_id=saved.id AND NOT(m.run_id=ANY(visible))) OR EXISTS(SELECT 1 FROM public.comparison_accounting a WHERE a.capture_id=saved.id AND NOT(a.run_id=ANY(visible))) THEN 'partial_unavailable' ELSE 'complete' END,
 'accounting_coverage',CASE WHEN EXISTS(SELECT 1 FROM public.comparison_accounting a WHERE a.capture_id=saved.id AND NOT(a.run_id=ANY(visible))) THEN 'partial_unavailable' ELSE 'complete' END,
 'physical',COALESCE((SELECT jsonb_agg(u.body ORDER BY u.call_identity) FROM public.comparison_usage u WHERE u.capture_id=saved.id AND u.run_id=ANY(accounting)),'[]'::jsonb),
 'score_records',COALESCE((SELECT jsonb_agg(q.body ORDER BY q.run_id,q.body->>'rubric') FROM public.comparison_scores q WHERE q.capture_id=saved.id AND q.run_id=ANY(visible)),'[]'::jsonb),
 'allocations',COALESCE((SELECT jsonb_agg(a.body ORDER BY a.id) FROM public.comparison_allocations a WHERE a.capture_id=saved.id AND EXISTS(SELECT 1 FROM public.comparison_scores q WHERE q.capture_id=saved.id AND q.run_id=ANY(visible) AND q.body->>'batch_id'=a.body->>'batch_id')),'[]'::jsonb));
 SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
WITH inputs AS(SELECT f.* FROM public.comparison_members m CROSS JOIN LATERAL jsonb_to_record(m.run_fact) AS f(family text,purpose text,execution_mode text,configuration_revision text,status text,admitted_at timestamptz,terminal_at timestamptz) WHERE m.capture_id=saved.id AND m.run_id=ANY(visible))
SELECT family,purpose,execution_mode,configuration_revision,CASE WHEN grain_value='hour' THEN
 date_trunc('hour',admitted_at AT TIME ZONE timezone_value) AT TIME ZONE 'UTC'
 - ((admitted_at AT TIME ZONE timezone_value)-(admitted_at AT TIME ZONE 'UTC'))
 ELSE date_trunc('day',admitted_at AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,
 count(*) AS run_count,count(*) FILTER(WHERE status='completed') AS completed,
 count(*) FILTER(WHERE status='failed') AS failed,count(*) FILTER(WHERE status='cancelled') AS cancelled,
 count(*) FILTER(WHERE status IN ('new','queued','running','waiting')) AS pending,
 count(*) FILTER(WHERE status NOT IN ('completed','failed','cancelled','new','queued','running','waiting')) AS unknown,
 count(*) FILTER(WHERE status IN ('completed','failed') AND terminal_at>=admitted_at) AS latency_samples,
 count(*) FILTER(WHERE status IN ('completed','failed') AND (terminal_at IS NULL OR terminal_at<admitted_at)) AS latency_missing,
 percentile_cont(.5) WITHIN GROUP(ORDER BY extract(epoch FROM terminal_at-admitted_at)*1000)
 FILTER(WHERE status IN ('completed','failed') AND terminal_at>=admitted_at) AS latency_p50,
 percentile_cont(.95) WITHIN GROUP(ORDER BY extract(epoch FROM terminal_at-admitted_at)*1000)
 FILTER(WHERE status IN ('completed','failed') AND terminal_at>=admitted_at) AS latency_p95
FROM inputs GROUP BY family,purpose,execution_mode,configuration_revision,bucket
ORDER BY family,purpose,execution_mode,configuration_revision,bucket
 ) x;
 facts:=facts||jsonb_build_object('run_groups',section);
SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
 SELECT m.run_fact->>'family' AS family,m.run_fact->>'purpose' AS purpose,m.run_fact->>'execution_mode' AS execution_mode,m.run_fact->>'configuration_revision' AS configuration_revision,
 CASE WHEN grain_value='hour' THEN date_trunc('hour',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE 'UTC' - (((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value)-((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE 'UTC')) ELSE date_trunc('day',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,
 sum((m.interval_fact->>'activity_occupancy_ms')::numeric) AS activity_occupancy_ms,sum((m.interval_fact->>'activity_samples')::numeric) AS activity_samples,sum((m.interval_fact->>'activity_missing')::numeric) AS activity_missing,sum((m.interval_fact->>'tool_work_ms')::numeric) AS tool_work_ms,sum((m.interval_fact->>'tool_samples')::numeric) AS tool_samples,sum((m.interval_fact->>'tool_missing')::numeric) AS tool_missing,sum((m.interval_fact->>'tool_terminal')::numeric) AS tool_terminal,sum((m.interval_fact->>'tool_errors')::numeric) AS tool_errors,sum((m.interval_fact->>'tool_excluded')::numeric) AS tool_excluded,sum((m.interval_fact->>'tool_execution_errors')::numeric) AS tool_execution_errors,sum((m.interval_fact->>'tool_business_errors')::numeric) AS tool_business_errors,sum((m.interval_fact->>'tool_unknown')::numeric) AS tool_unknown,sum((m.interval_fact->>'tool_deferred')::numeric) AS tool_deferred,sum((m.interval_fact->>'tool_cancelled')::numeric) AS tool_cancelled FROM public.comparison_members m WHERE m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.interval_fact IS NOT NULL GROUP BY family,purpose,execution_mode,configuration_revision,bucket
 ) x;
 facts:=facts||jsonb_build_object('intervals',section);
SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
 SELECT m.run_fact->>'family' AS family,m.run_fact->>'purpose' AS purpose,m.run_fact->>'execution_mode' AS execution_mode,m.run_fact->>'configuration_revision' AS configuration_revision,
 CASE WHEN grain_value='hour' THEN date_trunc('hour',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE 'UTC' - (((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value)-((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE 'UTC')) ELSE date_trunc('day',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,
 sum((m.approval_fact->>'approval_wait_ms')::numeric) AS approval_wait_ms,sum((m.approval_fact->>'samples')::numeric) AS samples,sum((m.approval_fact->>'missing')::numeric) AS missing FROM public.comparison_members m WHERE m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.approval_fact IS NOT NULL GROUP BY family,purpose,execution_mode,configuration_revision,bucket
 ) x;
 facts:=facts||jsonb_build_object('approvals',section);

 result:=jsonb_build_object('comparison_id',saved.comparison_id,'revision',saved.revision,'revision_id',saved.id,'alignment_revision',saved.alignment_revision,
 'captured_at',saved.captured_at,'timezone',timezone_value,'metric_version',saved.metric_version,
 'coverage_changed',facts->>'coverage'<>'complete',
 'member_count',(SELECT count(*) FROM public.comparison_members m WHERE m.capture_id=saved.id AND m.run_id=ANY(visible)),
 'baseline_configuration',CASE WHEN EXISTS(SELECT 1 FROM public.comparison_scores q WHERE q.capture_id=saved.id AND q.run_id=ANY(visible) AND q.body->>'config_id'=saved.baseline_configuration) THEN saved.baseline_configuration END,
 'members',COALESCE((SELECT jsonb_agg(jsonb_build_object('run_id',m.run_id,'admission_configuration_id',m.run_fact->>'admission_configuration_id','cut',jsonb_build_object('formal_position',m.formal_position,'progress_position',m.progress_position,'observed_order',m.observed_order,'projection_revision',m.projection_revision,'projector_version',m.projector_version),'ordinal',m.ordinal) ORDER BY m.ordinal) FROM (SELECT m.* FROM public.comparison_members m WHERE m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.ordinal>after_ordinal ORDER BY m.ordinal LIMIT page_limit+1) m),'[]'::jsonb),
 'details',COALESCE((SELECT jsonb_agg(jsonb_build_object('run_id',m.run_id,'availability',CASE WHEN d.run_id IS NULL THEN 'retained_data_unavailable' ELSE 'available' END,'body',d.body)) FROM public.comparison_members m LEFT JOIN public.comparison_details d ON d.capture_id=m.capture_id AND d.run_id=m.run_id WHERE m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.run_id IN(SELECT value::uuid FROM jsonb_array_elements_text(p->'detail_run_ids'))),'[]'::jsonb),
 'alignments',COALESCE((SELECT jsonb_agg(jsonb_build_object('revision',a.revision,'supersedes',a.supersedes,'author',a.author,'created_at',a.created_at,'edit',edit) ORDER BY a.revision) FROM public.comparison_alignment_pairs a CROSS JOIN LATERAL(SELECT a.body AS edit) item WHERE a.capture_id=saved.id AND a.left_run_id=ANY(visible) AND a.right_run_id=ANY(visible)),'[]'::jsonb));
 RETURN jsonb_build_object('body',result,'facts',facts,'query',saved.query,'authority_revision',epoch,'manifest',stamp);
END $$
"""

RESOURCE_CURRENT = r"""
CREATE FUNCTION public.opencitadel_export_resource_rows(scope_value text,capture uuid,selected uuid[] DEFAULT NULL)
RETURNS TABLE(run_id uuid,available boolean) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE owner_value text;team_value text;
BEGIN
 team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END;
 RETURN QUERY WITH bindings AS MATERIALIZED(
  SELECT m.run_id,m.resources FROM public.export_resources m WHERE selected IS NULL AND m.scope_key=scope_value AND m.capture_id=capture
  UNION ALL SELECT m.run_id,m.resources FROM (SELECT DISTINCT unnest(selected) AS run_id) target JOIN public.export_resources m ON m.scope_key=scope_value AND m.capture_id=capture AND m.run_id=target.run_id),
 flat AS MATERIALIZED(SELECT b.run_id,r->>'resource_kind' AS kind,r->>'resource_id' AS id,r->>'resource_version' AS version FROM bindings b CROSS JOIN LATERAL jsonb_array_elements(b.resources) r),
 unique_resources AS MATERIALIZED(SELECT DISTINCT f.kind,f.id,f.version FROM flat f),
 states AS MATERIALIZED(SELECT f.kind,f.id,f.version,CASE f.kind
 WHEN 'file' THEN EXISTS(SELECT 1 FROM public.files t WHERE t.id=f.id AND t.content_digest=f.version AND t.content_available AND t.owner_user_id IS NOT DISTINCT FROM owner_value AND t.team_id IS NOT DISTINCT FROM team_value)
 WHEN 'knowledge_base' THEN EXISTS(SELECT 1 FROM public.knowledge_bases k JOIN public.knowledge_base_versions v ON v.knowledge_base_id=k.id WHERE k.id=f.id AND v.id=f.version AND k.deleted_at IS NULL AND v.published_at IS NOT NULL AND v.state IN ('ready','degraded') AND k.owner_user_id IS NOT DISTINCT FROM owner_value AND k.team_id IS NOT DISTINCT FROM team_value)
 WHEN 'artifact' THEN EXISTS(SELECT 1 FROM public.artifacts a JOIN public.sessions s ON s.id=a.session_id WHERE a.id=f.id AND s.deleted_at IS NULL AND s.owner_user_id IS NOT DISTINCT FROM owner_value AND s.team_id IS NOT DISTINCT FROM team_value AND f.version ~ '^[1-9][0-9]*$' AND jsonb_array_length(a.version_refs)>=CASE WHEN length(f.version)<10 THEN f.version::integer ELSE 2147483647 END)
 WHEN 'execution_content' THEN EXISTS(SELECT 1 FROM public.execution_public_content c WHERE c.scope_key=scope_value AND c.content_id::text=f.id AND c.content_digest=f.version AND NOT c.redacted
 AND EXISTS(SELECT 1 FROM public.execution_content_bindings b WHERE b.scope_key=c.scope_key AND b.content_id=c.content_id)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c.citation_refs) citation WHERE citation->>'availability' IS DISTINCT FROM 'available' OR
 CASE WHEN citation->>'resource_kind'='file' THEN NOT EXISTS(SELECT 1 FROM public.files t WHERE t.id=citation->>'file_id' AND t.content_digest=citation->>'content_digest' AND t.object_identity::text=citation->>'object_identity' AND t.content_available AND t.owner_user_id IS NOT DISTINCT FROM owner_value AND t.team_id IS NOT DISTINCT FROM team_value)
 ELSE NOT EXISTS(SELECT 1 FROM public.knowledge_bases k JOIN public.knowledge_base_versions v ON v.knowledge_base_id=k.id
 JOIN public.knowledge_base_version_documents d ON d.knowledge_base_id=k.id AND d.version_id=v.id
 JOIN public.knowledge_document_revisions q ON q.id=d.document_revision_id AND q.document_id=d.document_id
 JOIN public.knowledge_documents doc ON doc.id=d.document_id AND doc.kb_id=k.id
 WHERE k.id=citation->>'knowledge_base_id' AND k.deleted_at IS NULL AND k.owner_user_id IS NOT DISTINCT FROM owner_value AND k.team_id IS NOT DISTINCT FROM team_value
 AND v.id=citation->>'version_id' AND v.published_at IS NOT NULL AND v.state IN ('ready','degraded')
 AND d.document_id=citation->>'doc_id' AND d.document_revision_id=citation->>'document_revision_id' AND d.state='indexed' AND q.state='indexed') END))
 ELSE false END AS available FROM unique_resources f)
 SELECT b.run_id,COALESCE(bool_and(COALESCE(states.available,false)) FILTER(WHERE flat.run_id IS NOT NULL),true)
 FROM bindings b LEFT JOIN flat ON flat.run_id=b.run_id LEFT JOIN states USING(kind,id,version)
 GROUP BY b.run_id;
END $$
"""

CURRENT = r"""
CREATE FUNCTION public.opencitadel_export_current_rows(scope_value text,capture uuid,selected uuid[] DEFAULT NULL)
RETURNS TABLE(run_id uuid,available boolean) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; team text; owner_value text;
BEGIN
 actor:=current_setting('app.user_id',true);team:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team IS NULL THEN actor ELSE NULL END;
 s:=scope_value;
 IF NOT EXISTS(SELECT 1 FROM public.execution_exports c WHERE c.id=capture AND c.scope_key=s) THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 RETURN QUERY
 SELECT m.run_id,
 (NOT m.required_run OR EXISTS(SELECT 1 FROM public.execution_view_runs r WHERE r.scope_key=s AND r.run_id=m.run_id))
 AND (m.source->>'entity_type' IS DISTINCT FROM 'session' OR EXISTS(SELECT 1 FROM public.sessions t WHERE t.id=m.source->>'entity_id' AND t.deleted_at IS NULL AND t.owner_user_id IS NOT DISTINCT FROM owner_value AND t.team_id IS NOT DISTINCT FROM team))
 AND resource_state.available
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.pins) required WHERE NOT EXISTS(
 SELECT 1 FROM public.resource_pins p WHERE p.scope_key=s AND p.owner_kind=required->>'owner_kind' AND p.owner_id=required->>'owner_id' AND p.resource_kind=required->>'resource_kind' AND p.resource_id=required->>'resource_id' AND p.resource_version=required->>'resource_version' AND p.available))

 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.owners) o WHERE
 CASE o->>'kind'
 WHEN 'dataset_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_dataset_versions d JOIN public.analysis_dataset_pin_coverage proof ON proof.scope_key=d.scope_key AND proof.version_id=d.id WHERE d.scope_key=s AND d.id::text=o->>'id')
 OR EXISTS(SELECT 1 FROM public.evaluation_version_cases vc JOIN public.evaluation_case_revisions cr ON cr.scope_key=vc.scope_key AND cr.id=vc.case_revision_id LEFT JOIN public.evaluation_object_intents ob ON ob.scope_key=cr.scope_key AND ob.id=cr.object_id WHERE vc.scope_key=s AND vc.version_id::text=o->>'id' AND (ob.id IS NULL OR ob.cleaned_at IS NOT NULL))
 WHEN 'recording_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_recording_versions rv WHERE rv.scope_key=s AND rv.id::text=o->>'id') OR EXISTS(SELECT 1 FROM public.evaluation_recording_slots rs LEFT JOIN public.evaluation_recording_objects ro ON ro.scope_key=rs.scope_key AND ro.id=rs.object_id WHERE rs.scope_key=s AND rs.version_id::text=o->>'id' AND (ro.id IS NULL OR ro.cleaned_at IS NOT NULL))
 WHEN 'config_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_config_versions cv WHERE cv.scope_key=s AND cv.id::text=o->>'id')
 WHEN 'batch' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_batches b WHERE b.scope_key=s AND b.id::text=o->>'id' AND b.suite_version::text=o->>'suite_version')
 WHEN 'batch_result' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_batch_results r WHERE r.scope_key=s AND r.id::text=o->>'id' AND r.batch_id::text=o->>'batch_id' AND r.case_revision_id::text=o->>'case_id' AND r.config_version_id::text=o->>'config_id')
 WHEN 'case_revision' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_version_cases v JOIN public.evaluation_case_revisions c ON c.scope_key=v.scope_key AND c.id=v.case_revision_id JOIN public.evaluation_object_intents ob ON ob.scope_key=c.scope_key AND ob.id=c.object_id AND ob.cleaned_at IS NULL WHERE v.scope_key=s AND v.version_id::text=o->>'dataset_version' AND v.case_revision_id::text=o->>'id')
 WHEN 'suite_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_suite_versions v WHERE v.scope_key=s AND v.id::text=o->>'id') WHEN 'rubric_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_rubric_versions v WHERE v.scope_key=s AND v.id::text=o->>'id') ELSE true END)
 FROM (SELECT r.* FROM public.export_resources r WHERE selected IS NULL AND r.capture_id=capture AND r.scope_key=s
 UNION ALL SELECT r.* FROM (SELECT DISTINCT unnest(selected) AS run_id) target JOIN public.export_resources r ON r.scope_key=s AND r.capture_id=capture AND r.run_id=target.run_id) m
 JOIN public.opencitadel_export_resource_rows(s,capture,selected) resource_state ON resource_state.run_id=m.run_id;
END $$
"""

TABLES = {
    "execution_exports": """id uuid PRIMARY KEY, scope_key text NOT NULL, caller_id text NOT NULL,
      principal jsonb NOT NULL, owner_scope jsonb NOT NULL, request jsonb NOT NULL,
      status text NOT NULL CHECK(status IN ('capturing','queued','running','ready','failed','invalidated','expired')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '24 hours',
      lease_token uuid, lease_until timestamptz, header jsonb, capture_bytes bigint NOT NULL DEFAULT 0,
      size bigint, digest text, failure_code text, UNIQUE(scope_key,id)""",
    "export_receipts": """scope_key text NOT NULL, caller_id text NOT NULL, request_id text NOT NULL,
      fingerprint text NOT NULL, export_id uuid NOT NULL REFERENCES execution_exports(id),
      PRIMARY KEY(scope_key,caller_id,request_id)""",
    "export_quota_serializers": """scope_key text NOT NULL,caller_id text NOT NULL,revision bigint NOT NULL DEFAULT 0,
      PRIMARY KEY(scope_key,caller_id)""",
    "export_staging": """capture_id uuid PRIMARY KEY, comparison_id uuid NOT NULL UNIQUE,
      scope_key text NOT NULL, caller_id text NOT NULL, request_id text NOT NULL,
      transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id()""",
    "export_resources": """capture_id uuid NOT NULL REFERENCES execution_exports(id),scope_key text NOT NULL,
      run_id uuid NOT NULL,source jsonb,resources jsonb NOT NULL,pins jsonb NOT NULL,owners jsonb NOT NULL,required_run boolean NOT NULL DEFAULT true,
      PRIMARY KEY(capture_id,run_id)""",
    "export_source_facts": """capture_id uuid PRIMARY KEY REFERENCES execution_exports(id),scope_key text NOT NULL,
      body jsonb NOT NULL CHECK(octet_length(body::text)<=268435456)""",
    "export_rows": """capture_id uuid NOT NULL REFERENCES execution_exports(id),scope_key text NOT NULL,
      ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 99999),body jsonb NOT NULL,
      PRIMARY KEY(capture_id,ordinal)""",
    "export_object_intents": """id uuid PRIMARY KEY,scope_key text NOT NULL,export_id uuid NOT NULL REFERENCES execution_exports(id),
      lease_token uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 127),
      object_key text NOT NULL UNIQUE,size integer NOT NULL CHECK(size BETWEEN 1 AND 1048576),digest text NOT NULL,
      write_completed boolean NOT NULL DEFAULT false,published boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),cleaned_at timestamptz,cleanup_checked_at timestamptz,
      UNIQUE(export_id,lease_token,ordinal)""",
    "export_download_uses": """id uuid PRIMARY KEY,scope_key text NOT NULL,export_id uuid NOT NULL REFERENCES execution_exports(id),
      caller_id text NOT NULL,expires_at timestamptz NOT NULL""",
}

STAGING = r"""
CREATE OR REPLACE FUNCTION public.opencitadel_comparison_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE revision_id uuid;
BEGIN
 IF TG_TABLE_NAME='comparison_revisions' THEN revision_id:=OLD.id; ELSE revision_id:=OLD.capture_id; END IF;
 IF TG_OP='DELETE' AND EXISTS(
   SELECT 1 FROM public.export_staging t JOIN public.comparison_revisions c ON c.id=t.capture_id
   WHERE t.capture_id=revision_id AND t.transaction_id=pg_current_xact_id()
     AND t.scope_key=c.scope_key AND t.caller_id=current_setting('app.user_id',true)
     AND c.caller_id=t.caller_id AND c.comparison_id=t.comparison_id AND NOT c.published
     AND c.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text)
 THEN RETURN OLD; END IF;
 IF TG_TABLE_NAME='comparison_revisions' THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
  IF OLD.published AND (to_jsonb(OLD)-'alignment_revision') IS DISTINCT FROM (to_jsonb(NEW)-'alignment_revision')
  THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
 IF EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=NEW.capture_id AND c.published)
 THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.opencitadel_export_acceptance_sealed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.execution_exports e WHERE e.id=NEW.id AND e.status='capturing')
 THEN RAISE EXCEPTION 'export_capture_not_sealed'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER export_acceptance_sealed AFTER INSERT ON public.execution_exports
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.opencitadel_export_acceptance_sealed();
CREATE FUNCTION public.opencitadel_export_staging_empty() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.export_staging WHERE capture_id=NEW.capture_id)
 THEN RAISE EXCEPTION 'export_staging_not_retired'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER export_staging_retired AFTER INSERT ON public.export_staging
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.opencitadel_export_staging_empty();
CREATE FUNCTION public.opencitadel_export_retire(capture uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE stage public.export_staging;
BEGIN
 SELECT * INTO stage FROM public.export_staging t WHERE t.capture_id=capture
  AND t.transaction_id=pg_current_xact_id() AND t.caller_id=current_setting('app.user_id',true);
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=capture AND NOT c.published
   AND c.comparison_id=stage.comparison_id AND c.caller_id=stage.caller_id AND c.scope_key=stage.scope_key
   AND c.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text)
 THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
 IF EXISTS(SELECT 1 FROM public.comparison_details WHERE capture_id=capture)
 OR EXISTS(SELECT 1 FROM public.comparison_artifacts WHERE capture_id=capture)
 OR EXISTS(SELECT 1 FROM public.comparison_diff_jobs WHERE capture_id=capture)
 OR EXISTS(SELECT 1 FROM public.comparison_alignments WHERE capture_id=capture)
 OR EXISTS(SELECT 1 FROM public.comparison_alignment_pairs WHERE capture_id=capture)
 OR EXISTS(SELECT 1 FROM public.resource_pins WHERE scope_key=stage.scope_key AND owner_kind='comparison_revision' AND owner_id=capture::text)
 THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
 DELETE FROM public.comparison_tool_facts WHERE capture_id=capture;
 DELETE FROM public.comparison_tool_captures WHERE capture_id=capture;
 DELETE FROM public.comparison_usage WHERE capture_id=capture;
 DELETE FROM public.comparison_scores WHERE capture_id=capture;
 DELETE FROM public.comparison_accounting_links WHERE capture_id=capture;
 DELETE FROM public.comparison_resources WHERE capture_id=capture;
 DELETE FROM public.comparison_allocations WHERE capture_id=capture;
 DELETE FROM public.comparison_accounting WHERE capture_id=capture;
 DELETE FROM public.comparison_members WHERE capture_id=capture;
 DELETE FROM public.comparison_revisions WHERE id=capture;
 DELETE FROM public.comparison_sets WHERE id=stage.comparison_id AND scope_key=stage.scope_key AND caller_id=stage.caller_id;
 DELETE FROM public.comparison_receipts WHERE scope_key=stage.scope_key AND caller_id=stage.caller_id
   AND request_id=stage.request_id AND response->>'revision_id'=capture::text;
 DELETE FROM public.export_staging WHERE capture_id=capture;
END $$;
"""

ACCEPT = r"""
CREATE FUNCTION public.opencitadel_export_accept(encoded text,signature text,source_encoded text,source_signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;actor text;fingerprint_value text;prior record;job uuid;source_value jsonb;
 source_request jsonb;capture uuid;source_record record;facts jsonb;visible uuid[];bytes bigint;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';actor:=current_setting('app.user_id',true);
 IF p->>'operation'<>'accept' OR p->'principal'->>'global_role'='auditor'
 OR p->'request'->>'format' NOT IN ('csv','json')
 OR length(p->'request'->>'request_id') NOT BETWEEN 1 AND 128
 THEN RAISE EXCEPTION 'invalid_export_request'; END IF;
 -- A durable common-row write forces stale RR snapshots to retry (40001),
 -- unlike an advisory lock which leaves their receipt/quota view stale.
 INSERT INTO public.export_quota_serializers(scope_key,caller_id,revision) VALUES(s,actor,1)
 ON CONFLICT(scope_key,caller_id) DO UPDATE SET revision=export_quota_serializers.revision+1;
 fingerprint_value:=COALESCE(p->'request'->>'request_fingerprint',encode(public.digest((p->'request'-'request_id')::text,'sha256'),'hex'));
 SELECT * INTO prior FROM public.export_receipts r WHERE r.scope_key=s AND r.caller_id=actor AND r.request_id=p->'request'->>'request_id';
 IF FOUND THEN
  IF prior.fingerprint<>fingerprint_value THEN RAISE EXCEPTION 'export_request_conflict'; END IF;
  RETURN jsonb_build_object('id',prior.export_id,'replayed',true);
 END IF;
 IF (SELECT count(*) FROM public.execution_exports e WHERE e.scope_key=s AND e.caller_id=actor AND e.expires_at>clock_timestamp())>=20
 OR (SELECT count(*) FROM public.execution_exports e WHERE e.scope_key=s AND e.caller_id=actor AND e.expires_at>clock_timestamp() AND e.status IN ('capturing','queued','running'))>=5
 THEN RAISE EXCEPTION 'export_quota_exceeded'; END IF;
 job:=public.gen_random_uuid();
 INSERT INTO public.execution_exports(id,scope_key,caller_id,principal,owner_scope,request,status)
 VALUES(job,s,actor,p->'principal',p->'owner_scope',p->'request','capturing');
 IF p->'request'->>'source_kind'='filter' THEN
  source_request:=source_encoded::jsonb;
  IF source_request->>'scope' IS DISTINCT FROM s OR source_request->'principal' IS DISTINCT FROM p->'principal'
   OR source_request->>'comparison_id' IS NOT NULL OR source_request->'detail_run_ids' IS DISTINCT FROM '[]'::jsonb
   OR source_request->>'baseline_configuration' IS NOT NULL
   OR EXISTS(SELECT 1 FROM public.comparison_receipts r WHERE r.scope_key=s AND r.caller_id=actor AND r.request_id=source_request->>'request_id')
  THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
  source_value:=public.opencitadel_comparison_materialize(source_encoded,source_signature);
  capture:=(source_value->>'revision_id')::uuid;
  SELECT * INTO source_record FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s AND c.caller_id=actor
    AND NOT c.published AND c.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text;
  IF NOT FOUND OR source_value->>'replayed'='true' THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
  INSERT INTO public.export_staging(capture_id,comparison_id,scope_key,caller_id,request_id)
  VALUES(capture,source_record.comparison_id,s,actor,source_request->>'request_id');
  RETURN jsonb_build_object('id',job,'capture_id',capture,'comparison_id',source_record.comparison_id,'revision',source_record.revision,'staging',true,'replayed',false);
 ELSIF p->'request'->>'source_kind'='comparison' THEN
  SELECT * INTO source_record FROM public.comparison_revisions c WHERE c.scope_key=s
    AND c.comparison_id=(p->'request'->>'comparison_id')::uuid AND c.revision=(p->'request'->>'revision')::integer AND c.published;
  IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
  RETURN jsonb_build_object('id',job,'capture_id',source_record.id,'comparison_id',source_record.comparison_id,'revision',source_record.revision,'staging',false,'replayed',false);
 ELSIF p->'request'->>'source_kind'='batch' THEN
  RETURN jsonb_build_object('id',job,'replayed',false);
 ELSE RAISE EXCEPTION 'invalid_export_source'; END IF;
END $$;
"""

CAPTURE = r"""
CREATE FUNCTION public.opencitadel_export_copy(encoded text,signature text,read_encoded text,read_signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;job public.execution_exports;capture uuid;saved record;facts jsonb;visible uuid[];bytes bigint;source_value jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 SELECT * INTO job FROM public.execution_exports e WHERE e.id=(p->>'export_id')::uuid AND e.scope_key=s
   AND e.caller_id=current_setting('app.user_id',true) AND e.status='capturing' AND e.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'export_not_found'; END IF;
 capture:=(p->>'capture_id')::uuid;
 SELECT * INTO saved FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s
 AND ((job.request->>'source_kind'='comparison' AND c.published AND c.comparison_id=(job.request->>'comparison_id')::uuid AND c.revision=(job.request->>'revision')::integer)
 OR (job.request->>'source_kind' IN ('filter','batch') AND EXISTS(SELECT 1 FROM public.export_staging t WHERE t.capture_id=c.id AND t.scope_key=s AND t.caller_id=job.caller_id AND t.transaction_id=pg_current_xact_id())));
 IF NOT FOUND OR read_encoded::jsonb->>'comparison_id' IS DISTINCT FROM saved.comparison_id::text
 OR read_encoded::jsonb->>'revision' IS DISTINCT FROM saved.revision::text
 OR read_encoded::jsonb->>'scope' IS DISTINCT FROM s
 OR read_encoded::jsonb->'principal' IS DISTINCT FROM p->'principal'
 THEN RAISE EXCEPTION 'export_capture_mismatch'; END IF;
 source_value:=public.opencitadel_export_source_facts(read_encoded,read_signature);
 source_value:=jsonb_set(source_value,'{facts,chart_facts}',public.opencitadel_export_source_charts(read_encoded,read_signature));
 SELECT COALESCE(array_agg(run_id) FILTER(WHERE available),ARRAY[]::uuid[]) INTO visible FROM public.opencitadel_comparison_current_rows(s,capture);
 INSERT INTO public.export_resources(capture_id,scope_key,run_id,source,resources,pins,owners)
 SELECT job.id,s,r.run_id,r.source,r.resources,r.pins,r.owners FROM public.comparison_resources r WHERE r.capture_id=capture AND r.run_id=ANY(visible);
 INSERT INTO public.export_rows(capture_id,scope_key,ordinal,body)
 SELECT job.id,s,(row_number() OVER(ORDER BY m.ordinal)-1)::integer,
 jsonb_build_object('run_id',m.run_id,'cut',jsonb_build_object('formal_position',m.formal_position,'progress_position',m.progress_position,'observed_order',m.observed_order,'projection_revision',m.projection_revision,'projector_version',m.projector_version,'generation',m.generation),
 'coverage',m.coverage,'run_fact',m.run_fact,'interval_fact',m.interval_fact,'approval_fact',m.approval_fact,
 'usage',COALESCE((SELECT jsonb_agg(u.body) FROM public.comparison_usage u WHERE u.capture_id=capture AND u.run_id=m.run_id),'[]'::jsonb),
 'scores',COALESCE((SELECT jsonb_agg(q.body) FROM public.comparison_scores q WHERE q.capture_id=capture AND q.run_id=m.run_id),'[]'::jsonb))
 FROM public.comparison_members m WHERE m.capture_id=capture AND m.run_id=ANY(visible);
 INSERT INTO public.export_source_facts(capture_id,scope_key,body) VALUES(job.id,s,source_value);
 SELECT COALESCE(sum(octet_length(body::text)),0) INTO bytes FROM public.export_rows WHERE capture_id=job.id;
 bytes:=bytes+octet_length(source_value::text)+(SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM public.export_resources r WHERE capture_id=job.id);
 IF bytes>268435456 OR bytes+(SELECT COALESCE(sum(capture_bytes),0) FROM public.execution_exports e WHERE e.scope_key=s AND e.caller_id=job.caller_id)>536870912
 THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
 UPDATE public.execution_exports SET capture_bytes=bytes WHERE id=job.id;
 IF EXISTS(SELECT 1 FROM public.export_staging WHERE capture_id=capture) THEN PERFORM public.opencitadel_export_retire(capture); END IF;
 RETURN source_value||jsonb_build_object('export_id',job.id,'expires_at',job.expires_at,'row_count',(SELECT count(*) FROM public.export_rows WHERE capture_id=job.id));
END $$;
"""
JOBS = r"""
CREATE FUNCTION public.opencitadel_export_jobs(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;actor text;job public.execution_exports;operation_name text;intent public.export_object_intents;
 epoch bigint;token uuid;items jsonb;page_rows jsonb;last_ordinal integer;available boolean;count_value integer;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';actor:=current_setting('app.user_id',true);operation_name:=p->>'operation';
 SELECT * INTO job FROM public.execution_exports e WHERE e.id=(p->>'export_id')::uuid AND e.scope_key=s AND e.caller_id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'export_not_found'; END IF;
 IF operation_name='release_use' THEN
  DELETE FROM public.export_download_uses u WHERE u.id=(p->>'use_id')::uuid AND u.export_id=job.id AND u.caller_id=actor;
  RETURN '{}'::jsonb;
 END IF;
 IF job.expires_at<=clock_timestamp() THEN
  UPDATE public.execution_exports SET status='expired',lease_until=NULL WHERE id=job.id;
  RETURN jsonb_build_object('id',job.id,'status','expired');
 END IF;
 available:=true;
 IF operation_name IN ('current','status','seal','publish','acquire_use','manifest','finish_use') THEN
  SELECT COALESCE(bool_and(r.available),true) INTO available FROM public.opencitadel_export_current_rows(s,job.id) r;
 END IF;
 IF public.opencitadel_analysis_authority(encoded,signature)<>epoch THEN RAISE EXCEPTION 'export_authorization_changed'; END IF;
 IF NOT available OR job.status='invalidated' THEN
  UPDATE public.execution_exports SET status='invalidated',lease_until=NULL WHERE id=job.id;
  RETURN jsonb_build_object('id',job.id,'status','invalidated');
 END IF;
 IF operation_name='current' THEN RETURN jsonb_build_object('id',job.id,'status',job.status); END IF;
 IF operation_name='status' THEN
  RETURN jsonb_build_object('id',job.id,'status',job.status,'created_at',job.created_at,'expires_at',job.expires_at,
    'format',job.request->>'format','failure_code',job.failure_code,'row_count',job.header->'metadata'->'data_row_count');
 END IF;
 IF operation_name='seal' THEN
  IF job.status<>'capturing' OR NOT EXISTS(SELECT 1 FROM public.execution_exports e WHERE e.id=job.id AND e.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text)
   OR NOT EXISTS(SELECT 1 FROM public.export_source_facts WHERE capture_id=job.id)
   OR (p->'header'->'metadata'->>'data_row_count')::integer<>(SELECT count(*) FROM public.export_rows WHERE capture_id=job.id)
   OR octet_length((p->'header')::text)>9437184 THEN RAISE EXCEPTION 'export_capture_mismatch'; END IF;
  IF job.capture_bytes+octet_length((p->'header')::text)>268435456
   OR octet_length((p->'header')::text)+(SELECT COALESCE(sum(capture_bytes),0) FROM public.execution_exports WHERE scope_key=s AND caller_id=actor)>536870912
  THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
  UPDATE public.execution_exports SET header=p->'header',capture_bytes=capture_bytes+octet_length((p->'header')::text),status='queued' WHERE id=job.id;
  BEGIN
   INSERT INTO public.export_receipts(scope_key,caller_id,request_id,fingerprint,export_id)
   VALUES(s,actor,job.request->>'request_id',COALESCE(job.request->>'request_fingerprint',encode(public.digest((job.request-'request_id')::text,'sha256'),'hex')),job.id);
  EXCEPTION WHEN unique_violation THEN
   RAISE EXCEPTION 'export_receipt_snapshot_retry' USING ERRCODE='40001';
  END;
  RETURN jsonb_build_object('id',job.id,'status','queued','created_at',job.created_at,'expires_at',job.expires_at);
 END IF;
 IF operation_name IN ('acquire_use','manifest','finish_use') THEN
  IF job.status<>'ready' THEN RAISE EXCEPTION 'export_not_ready'; END IF;
  IF operation_name='acquire_use' THEN
   DELETE FROM public.export_download_uses WHERE export_id=job.id AND expires_at<=clock_timestamp();
   IF (SELECT count(*) FROM public.export_download_uses WHERE export_id=job.id)>=5 THEN RAISE EXCEPTION 'export_quota_exceeded'; END IF;
   token:=public.gen_random_uuid();
   INSERT INTO public.export_download_uses(id,scope_key,export_id,caller_id,expires_at)
   VALUES(token,s,job.id,actor,LEAST(job.expires_at,clock_timestamp()+interval '10 minutes'));
  ELSE
   token:=(p->>'use_id')::uuid;
   IF NOT EXISTS(SELECT 1 FROM public.export_download_uses u WHERE u.id=token AND u.export_id=job.id AND u.caller_id=actor AND u.expires_at>clock_timestamp())
   THEN RAISE EXCEPTION 'export_download_lease_lost'; END IF;
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('intent_id',i.id,'key',i.object_key,'ordinal',i.ordinal,'size',i.size,'digest',i.digest) ORDER BY i.ordinal),'[]'::jsonb)
   INTO items FROM public.export_object_intents i WHERE i.export_id=job.id AND i.published AND i.cleaned_at IS NULL;
  RETURN jsonb_build_object('id',job.id,'status','ready','use_id',token,'size',job.size,'digest',job.digest,'format',job.request->>'format','chunks',items);
 END IF;
 IF job.status<>'running' OR job.lease_token IS DISTINCT FROM (p->>'lease_token')::uuid OR job.lease_until<=clock_timestamp()
 THEN RAISE EXCEPTION 'export_lease_lost'; END IF;
 IF operation_name='renew' THEN
  UPDATE public.execution_exports SET lease_until=LEAST(expires_at,clock_timestamp()+interval '120 seconds') WHERE id=job.id;
  RETURN '{}'::jsonb;
 ELSIF operation_name='header' THEN RETURN job.header;
 ELSIF operation_name='page' THEN
  IF (p->>'after')::integer < -1 OR (p->>'limit')::integer NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_export_page'; END IF;
  WITH candidates AS(SELECT ordinal,body,sum(octet_length(body::text)) OVER(ORDER BY ordinal) AS bytes
    FROM (SELECT ordinal,body FROM public.export_rows WHERE capture_id=job.id AND ordinal>(p->>'after')::integer ORDER BY ordinal LIMIT (p->>'limit')::integer) limited)
   SELECT COALESCE(jsonb_agg(body ORDER BY ordinal),'[]'::jsonb),max(ordinal) INTO page_rows,last_ordinal FROM candidates WHERE bytes<=1048576;
  IF last_ordinal IS NULL AND EXISTS(SELECT 1 FROM public.export_rows WHERE capture_id=job.id AND ordinal>(p->>'after')::integer)
  THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
  RETURN jsonb_build_object('rows',page_rows,'row_context',job.header->'row_context','next_after',CASE WHEN EXISTS(SELECT 1 FROM public.export_rows WHERE capture_id=job.id AND ordinal>last_ordinal) THEN last_ordinal ELSE NULL END);
 ELSIF operation_name='begin_chunk' THEN
  IF (p->>'size')::integer NOT BETWEEN 1 AND 1048576 OR (p->>'ordinal')::integer NOT BETWEEN 0 AND 127
   OR p->>'digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
  token:=public.gen_random_uuid();
  INSERT INTO public.export_object_intents(id,scope_key,export_id,lease_token,ordinal,object_key,size,digest)
  VALUES(token,s,job.id,job.lease_token,(p->>'ordinal')::integer,'execution-exports/'||job.id::text||'/'||token::text,(p->>'size')::integer,p->>'digest') RETURNING * INTO intent;
  RETURN jsonb_build_object('intent_id',intent.id,'key',intent.object_key,'ordinal',intent.ordinal,'size',intent.size,'digest',intent.digest);
 ELSIF operation_name='chunk_guard' THEN
  SELECT * INTO intent FROM public.export_object_intents i WHERE i.id=(p->>'intent_id')::uuid AND i.export_id=job.id AND i.lease_token=job.lease_token AND i.cleaned_at IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'export_lease_lost'; END IF;
  RETURN '{}'::jsonb;
 ELSIF operation_name='publish' THEN
  SELECT count(*),COALESCE(jsonb_agg(jsonb_build_object('intent_id',i.id,'key',i.object_key,'ordinal',i.ordinal,'size',i.size,'digest',i.digest) ORDER BY i.ordinal),'[]'::jsonb)
   INTO count_value,items FROM public.export_object_intents i WHERE i.export_id=job.id AND i.lease_token=job.lease_token AND i.cleaned_at IS NULL AND i.write_completed;
  IF count_value NOT BETWEEN 1 AND 128 OR items IS DISTINCT FROM p->'chunks'
   OR (SELECT sum((x->>'size')::bigint) FROM jsonb_array_elements(items) x)<>(p->>'size')::bigint
   OR (p->>'size')::bigint NOT BETWEEN 1 AND 134217728 OR p->>'digest' !~ '^[0-9a-f]{64}$'
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(items) WITH ORDINALITY x(v,n) WHERE (v->>'ordinal')::integer<>n-1)
  THEN RAISE EXCEPTION 'export_capture_mismatch'; END IF;
  UPDATE public.export_object_intents SET published=true WHERE export_id=job.id AND lease_token=job.lease_token;
  UPDATE public.execution_exports SET status='ready',size=(p->>'size')::bigint,digest=p->>'digest',lease_until=NULL WHERE id=job.id;
  RETURN jsonb_build_object('id',job.id,'status','ready');
 ELSE RAISE EXCEPTION 'invalid_export_operation'; END IF;
END $$;
"""

KERNEL = r"""
CREATE FUNCTION public.opencitadel_export_kernel(operation_name text,target uuid DEFAULT NULL,token uuid DEFAULT NULL,reason text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE job public.execution_exports;item public.export_object_intents;result jsonb;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true)<>'system'
 OR current_setting('app.system_actor',true)<>'execution-kernel' THEN RAISE EXCEPTION 'export_kernel_required'; END IF;
 IF operation_name='claim' THEN
  SELECT * INTO job FROM public.execution_exports e WHERE e.expires_at>clock_timestamp()
   AND (e.status='queued' OR (e.status='running' AND e.lease_until<=clock_timestamp()))
   ORDER BY e.created_at,e.id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.execution_exports SET status='running',lease_token=public.gen_random_uuid(),lease_until=LEAST(expires_at,clock_timestamp()+interval '120 seconds') WHERE id=job.id RETURNING * INTO job;
  RETURN jsonb_build_object('export_id',job.id,'token',job.lease_token,'scope',job.owner_scope,'principal',job.principal,'expires_at',job.expires_at);
 ELSIF operation_name='write_completed' THEN
  -- Enforce the same ownership boundary even for direct kernel callers.
  PERFORM pg_advisory_xact_lock(hashtextextended('export-object:'||target::text,0));
  UPDATE public.export_object_intents SET write_completed=true WHERE id=target AND lease_token=token AND cleaned_at IS NULL;
  RETURN '{}'::jsonb;
 ELSIF operation_name='fail' THEN
  UPDATE public.execution_exports SET status='failed',failure_code=CASE WHEN reason IN ('export_capacity_exceeded','export_capture_mismatch','export_corrupt_object') THEN reason ELSE 'export_generation_failed' END,lease_until=NULL
   WHERE id=target AND lease_token=token AND status='running' AND lease_until>clock_timestamp();
  RETURN '{}'::jsonb;
 ELSIF operation_name='retire_captures' THEN
  FOR job IN SELECT e.* FROM public.execution_exports e WHERE e.capture_bytes>0
    AND (e.expires_at<=clock_timestamp() OR e.status IN ('failed','invalidated','expired'))
    AND NOT EXISTS(SELECT 1 FROM public.export_download_uses u WHERE u.export_id=e.id AND u.expires_at>clock_timestamp())
    AND NOT EXISTS(SELECT 1 FROM public.export_object_intents i WHERE i.export_id=e.id AND i.cleaned_at IS NULL)
    AND NOT(e.status='running' AND e.lease_until>clock_timestamp()) ORDER BY e.created_at LIMIT 100 FOR UPDATE SKIP LOCKED LOOP
   DELETE FROM public.export_rows WHERE capture_id=job.id;
   DELETE FROM public.export_resources WHERE capture_id=job.id;
   DELETE FROM public.export_source_facts WHERE capture_id=job.id;
   DELETE FROM public.export_download_uses WHERE export_id=job.id;
   UPDATE public.execution_exports SET header=NULL,capture_bytes=0,status=CASE WHEN expires_at<=clock_timestamp() THEN 'expired' ELSE status END WHERE id=job.id;
  END LOOP;
  RETURN '{}'::jsonb;
 ELSIF operation_name='cleanup_inventory' THEN
  SELECT COALESCE(jsonb_agg(to_jsonb(i)),'[]'::jsonb) INTO result FROM(
   SELECT ob.id,ob.object_key FROM public.export_object_intents ob JOIN public.execution_exports e ON e.id=ob.export_id
   WHERE ob.cleaned_at IS NULL AND NOT EXISTS(SELECT 1 FROM public.export_download_uses u WHERE u.export_id=e.id AND u.expires_at>clock_timestamp())
    AND NOT(e.status='running' AND e.lease_until>clock_timestamp())
    AND ((e.expires_at<=clock_timestamp() OR e.status IN ('failed','invalidated','expired')) OR (NOT ob.published AND ob.created_at<clock_timestamp()-interval '1 hour'))
   ORDER BY ob.cleanup_checked_at NULLS FIRST,ob.created_at,ob.id LIMIT 100)i;
  RETURN result;
 ELSIF operation_name IN ('cleanup_guard','cleaned') THEN
  SELECT ob.* INTO item FROM public.export_object_intents ob JOIN public.execution_exports e ON e.id=ob.export_id
   WHERE ob.id=target AND ob.cleaned_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM public.export_download_uses u WHERE u.export_id=e.id AND u.expires_at>clock_timestamp())
   AND NOT(e.status='running' AND e.lease_until>clock_timestamp())
   AND ((e.expires_at<=clock_timestamp() OR e.status IN ('failed','invalidated','expired')) OR (NOT ob.published AND ob.created_at<clock_timestamp()-interval '1 hour')) FOR UPDATE OF ob,e;
  IF NOT FOUND THEN RETURN jsonb_build_object('eligible',false); END IF;
  IF operation_name='cleaned' THEN
   UPDATE public.export_object_intents SET cleanup_checked_at=clock_timestamp(),cleaned_at=CASE WHEN write_completed THEN clock_timestamp() END WHERE id=target;
  END IF;
  RETURN jsonb_build_object('eligible',true,'key',item.object_key);
 ELSE RAISE EXCEPTION 'invalid_export_operation'; END IF;
END $$;
"""


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    quote = bind.dialect.identifier_preparer.quote
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    for name, ddl in TABLES.items():
        bind.execute(sa.text(f"CREATE TABLE public.{name}({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{name} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
        valid = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'"
        bind.execute(
            sa.text(
                f"CREATE POLICY export_scope ON public.{name} TO {quote(owner)} USING ({valid} AND {scoped}) WITH CHECK ({valid} AND {scoped})"
            )
        )
        if name != "export_staging":
            bind.execute(
                sa.text(
                    f"CREATE POLICY export_kernel ON public.{name} TO {quote(owner)} USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')"
                )
            )
    for sql in (
        STAGING,
        SOURCE_FACTS,
        SOURCE_CHARTS,
        RESOURCE_CURRENT,
        CURRENT,
        ACCEPT,
        CAPTURE,
        JOBS,
        KERNEL,
        BATCH_STAGE,
        BATCH_CONTEXT,
        BATCH_PROMOTE,
    ):
        bind.execute(sa.text(sql))
    for name, signature, access in (
        ("acceptance_sealed", "", ""),
        ("staging_empty", "", ""),
        ("retire", "uuid", ""),
        ("source_facts", "text,text", ""),
        ("source_charts", "text,text", ""),
        ("resource_rows", "text,uuid,uuid[]", ""),
        ("current_rows", "text,uuid,uuid[]", ""),
        ("accept", "text,text,text,text", "api"),
        ("copy", "text,text,text,text", "api"),
        ("stage", "text,text,text,text", "api"),
        ("batch_context", "text,text", "api"),
        ("batch_promote", "text,text", "api"),
        ("jobs", "text,text", "both"),
        ("kernel", "text,uuid,uuid,text", "kernel"),
    ):
        function = f"public.opencitadel_export_{name}({signature})"
        bind.execute(
            sa.text(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        if access in {"api", "both"}:
            bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(api)}"))
        if access in {"kernel", "both"}:
            bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(kernel)}"))
    bind.execute(sa.text("CREATE INDEX ON public.execution_exports(status,created_at)"))
    bind.execute(
        sa.text("CREATE INDEX ON public.execution_exports(scope_key,caller_id,expires_at)")
    )
    bind.execute(
        sa.text("CREATE INDEX ON public.export_object_intents(export_id,lease_token,ordinal)")
    )
    bind.execute(sa.text("CREATE INDEX ON public.export_download_uses(export_id,expires_at)"))


def downgrade():
    raise RuntimeError("export retention is forward-only")


BATCH_STAGE = r"""
CREATE FUNCTION public.opencitadel_export_stage(encoded text,signature text,source_encoded text,source_signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;actor text;job public.execution_exports;source_request jsonb;source_value jsonb;capture uuid;source_record record;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';actor:=current_setting('app.user_id',true);
 SELECT * INTO job FROM public.execution_exports e WHERE e.id=(p->>'export_id')::uuid AND e.scope_key=s AND e.caller_id=actor AND e.status='capturing' AND e.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text;
 IF NOT FOUND OR job.request->>'source_kind'<>'batch' THEN RAISE EXCEPTION 'export_not_found'; END IF;
  source_request:=source_encoded::jsonb;
  IF source_request->>'scope' IS DISTINCT FROM s OR source_request->'principal' IS DISTINCT FROM p->'principal'
   OR source_request->>'comparison_id' IS NOT NULL OR source_request->'detail_run_ids' IS DISTINCT FROM '[]'::jsonb
   OR source_request->>'baseline_configuration' IS NOT NULL
   OR EXISTS(SELECT 1 FROM public.comparison_receipts r WHERE r.scope_key=s AND r.caller_id=actor AND r.request_id=source_request->>'request_id')
  THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
  source_value:=public.opencitadel_comparison_materialize(source_encoded,source_signature);
  capture:=(source_value->>'revision_id')::uuid;
  SELECT * INTO source_record FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s AND c.caller_id=actor
    AND NOT c.published AND c.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text;
  IF NOT FOUND OR source_value->>'replayed'='true' THEN RAISE EXCEPTION 'export_staging_invalid'; END IF;
  INSERT INTO public.export_staging(capture_id,comparison_id,scope_key,caller_id,request_id)
  VALUES(capture,source_record.comparison_id,s,actor,source_request->>'request_id');
  RETURN jsonb_build_object('id',job.id,'capture_id',capture,'comparison_id',source_record.comparison_id,'revision',source_record.revision,'staging',true,'replayed',false);

END $$;
"""

BATCH_CONTEXT = r"""
CREATE FUNCTION public.opencitadel_export_batch_context(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;job public.execution_exports;snapshot jsonb;suite record;runs jsonb;first_at timestamptz;last_at timestamptz;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 SELECT * INTO job FROM public.execution_exports e WHERE e.id=(p->>'export_id')::uuid AND e.scope_key=s AND e.caller_id=current_setting('app.user_id',true) AND e.status='capturing' AND e.xmin::text=(pg_current_xact_id()::text::numeric % 4294967296)::text;
 IF NOT FOUND OR job.request->>'source_kind'<>'batch' THEN RAISE EXCEPTION 'export_not_found'; END IF;
 SELECT body INTO snapshot FROM public.evaluation_summary_snapshots WHERE id=(p->>'snapshot_id')::uuid AND scope_key=s AND caller_id=job.caller_id
  AND batch_id=(job.request->>'batch_id')::uuid AND expires_at>clock_timestamp();
 IF snapshot IS NULL OR snapshot->>'source' IS DISTINCT FROM job.request->>'source'
 OR snapshot->>'dimension' IS DISTINCT FROM job.request->>'dimension' OR snapshot->>'rubric_id' IS DISTINCT FROM job.request->>'rubric_id'
 OR snapshot->>'evaluation_revision' IS DISTINCT FROM job.request->>'evaluation_revision'
 OR (job.request->>'snapshot_id' IS NOT NULL AND job.request->>'snapshot_id' IS DISTINCT FROM snapshot->>'id')
 THEN RAISE EXCEPTION 'export_snapshot_unavailable'; END IF;
 SELECT v.*,b.revision AS batch_revision INTO suite FROM public.evaluation_batches b JOIN public.evaluation_suite_versions v ON v.scope_key=b.scope_key AND v.id=b.suite_version WHERE b.scope_key=s AND b.id=(job.request->>'batch_id')::uuid;
 IF NOT FOUND OR (job.request->>'batch_revision' IS NOT NULL AND (job.request->>'batch_revision')::bigint<>suite.batch_revision)
 THEN RAISE EXCEPTION 'export_snapshot_unavailable'; END IF;
 -- A live E11 snapshot attests score/usage cuts, not a historical batch revision.
 IF (SELECT count(*) FROM public.evaluation_batch_results WHERE scope_key=s AND batch_id=(job.request->>'batch_id')::uuid)>5000 OR jsonb_array_length(snapshot->'rows')>5000 THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
 WITH ids AS(
  SELECT (r->>'run_id')::uuid AS id FROM jsonb_array_elements(snapshot->'rows') r WHERE r->>'run_id' IS NOT NULL AND COALESCE((r->>'run_revision')::bigint,0)>0
  UNION SELECT (r->>'score_run_id')::uuid FROM jsonb_array_elements(snapshot->'rows') r WHERE r->>'score_run_id' IS NOT NULL
  UNION SELECT (a->>'run_id')::uuid FROM jsonb_array_elements(snapshot->'rows') r CROSS JOIN LATERAL jsonb_array_elements(r->'attempts') a WHERE COALESCE((a->>'run_revision')::bigint,0)>0
  UNION SELECT j.run_id FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.batch_id=(job.request->>'batch_id')::uuid AND j.created_at<=(snapshot->>'captured_at')::timestamptz AND (EXISTS(SELECT 1 FROM public.execution_view_runs v WHERE v.scope_key=s AND v.run_id=j.run_id) OR EXISTS(SELECT 1 FROM public.execution_model_dispatches d WHERE d.scope_key=s AND d.run_id=j.run_id)))
 SELECT COALESCE(jsonb_agg(ids.id ORDER BY ids.id),'[]'::jsonb),min(v.admitted_at),max(v.admitted_at) INTO runs,first_at,last_at FROM ids LEFT JOIN public.execution_view_runs v ON v.scope_key=s AND v.run_id=ids.id;
 RETURN jsonb_build_object('snapshot',snapshot,'run_ids',runs,'start',COALESCE(first_at,clock_timestamp())-interval '1 second','end',COALESCE(last_at,clock_timestamp())+interval '1 second',
  'dataset_version',suite.dataset_version,'execution_mode',suite.body->>'mode','suite_version',suite.id);
END $$;
"""

BATCH_PROMOTE = r"""
CREATE FUNCTION public.opencitadel_export_batch_promote(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;job public.execution_exports;context jsonb;snapshot jsonb;owner_list jsonb;resources_value jsonb;pins_value jsonb;bytes bigint;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 context:=public.opencitadel_export_batch_context(encoded,signature);snapshot:=context->'snapshot';
 SELECT * INTO job FROM public.execution_exports e WHERE e.id=(p->>'export_id')::uuid;
 IF NOT EXISTS(SELECT 1 FROM public.export_source_facts WHERE capture_id=job.id) THEN RAISE EXCEPTION 'export_capture_mismatch'; END IF;
 WITH owners AS(
  SELECT 'dataset_version'::text AS kind,context->>'dataset_version' AS id
  UNION SELECT 'config_version',r->>'config_id' FROM jsonb_array_elements(snapshot->'rows') r
  UNION SELECT 'config_version',v.body->>'judge_config_version' FROM public.evaluation_rubric_versions v WHERE v.scope_key=s AND v.id=(snapshot->>'rubric_id')::uuid AND v.body->>'judge_config_version' IS NOT NULL
  UNION SELECT 'config_version',j.config_id::text FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.batch_id=(job.request->>'batch_id')::uuid AND j.created_at<=(snapshot->>'captured_at')::timestamptz
  UNION SELECT 'suite_version',context->>'suite_version'
  UNION SELECT 'rubric_version',snapshot->>'rubric_id'
  UNION SELECT 'recording_version',v FROM public.evaluation_suite_versions t CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(t.body->'recording_versions','[]'::jsonb)) v WHERE t.scope_key=s AND t.id=(context->>'suite_version')::uuid)
 SELECT jsonb_agg(jsonb_build_object('kind',kind,'id',id)) INTO owner_list FROM owners;
 owner_list:=owner_list||jsonb_build_array(jsonb_build_object('kind','batch','id',job.request->>'batch_id','suite_version',context->>'suite_version'))
  ||COALESCE((SELECT jsonb_agg(jsonb_build_object('kind','batch_result','id',r->>'id','batch_id',job.request->>'batch_id','case_id',r->>'case_id','config_id',r->>'config_id')) FROM jsonb_array_elements(snapshot->'rows') r),'[]'::jsonb)
  ||COALESCE((SELECT jsonb_agg(DISTINCT jsonb_build_object('kind','case_revision','id',r->>'case_id','dataset_version',context->>'dataset_version')) FROM jsonb_array_elements(snapshot->'rows') r),'[]'::jsonb);

 SELECT COALESCE(jsonb_agg(DISTINCT jsonb_build_object('owner_kind',p.owner_kind,'owner_id',p.owner_id,'resource_kind',p.resource_kind,'resource_id',p.resource_id,'resource_version',p.resource_version)),'[]'::jsonb) INTO pins_value
 FROM public.analysis_required_pins p WHERE p.scope_key=s AND EXISTS(SELECT 1 FROM jsonb_array_elements(owner_list) o WHERE o->>'kind'=p.owner_kind AND o->>'id'=p.owner_id);
 WITH refs AS(
 SELECT e->>'resource_kind' AS kind,e->>'resource_id' AS id,e->>'resource_version' AS version FROM jsonb_array_elements(pins_value) e
 UNION SELECT e->>'resource_kind',e->>'resource_id',e->>'resource_version' FROM jsonb_array_elements(owner_list) o JOIN public.evaluation_config_versions c ON c.scope_key=s AND o->>'kind'='config_version' AND c.id::text=o->>'id' CROSS JOIN LATERAL jsonb_array_elements(COALESCE(c.body->'selection'->'resources','[]'::jsonb))e
 UNION SELECT e->>'resource_kind',e->>'resource_id',e->>'resource_version' FROM jsonb_array_elements(owner_list) o JOIN public.evaluation_recording_versions v ON v.scope_key=s AND o->>'kind'='recording_version' AND v.id::text=o->>'id' CROSS JOIN LATERAL jsonb_array_elements(COALESCE(v.body->'pins','[]'::jsonb))e
 UNION SELECT e->>'resource_kind',e->>'resource_id',e->>'resource_version' FROM jsonb_array_elements(snapshot->'rows') r
 JOIN LATERAL(SELECT v.id FROM public.evaluation_score_sets v WHERE v.scope_key=s AND v.result_id=(r->>'id')::uuid AND v.source=snapshot->>'source' AND v.rubric_revision=(snapshot->>'rubric_id')::uuid AND v.evaluation_revision<=(snapshot->>'evaluation_revision')::bigint ORDER BY v.evaluation_revision DESC LIMIT 1)head ON true
 JOIN public.evaluation_scores q ON q.scope_key=s AND q.set_id=head.id AND q.dimension=snapshot->>'dimension' CROSS JOIN LATERAL jsonb_array_elements(q.evidence)e)
 SELECT COALESCE(jsonb_agg(jsonb_build_object('resource_kind',kind,'resource_id',id,'resource_version',version)),'[]'::jsonb) INTO resources_value FROM refs;
 INSERT INTO public.export_resources(capture_id,scope_key,run_id,source,resources,pins,owners,required_run)
 VALUES(job.id,s,job.id,NULL,resources_value,pins_value,owner_list,false);
 IF EXISTS(SELECT 1 FROM public.opencitadel_export_current_rows(s,job.id) WHERE NOT available)
 THEN RAISE EXCEPTION 'export_snapshot_unavailable'; END IF;
 DELETE FROM public.export_rows WHERE capture_id=job.id;
 INSERT INTO public.export_rows(capture_id,scope_key,ordinal,body) SELECT job.id,s,n-1,r FROM jsonb_array_elements(snapshot->'rows') WITH ORDINALITY a(r,n);
 UPDATE public.export_source_facts SET body=context WHERE capture_id=job.id;
 SELECT COALESCE(sum(octet_length(body::text)),0) INTO bytes FROM public.export_rows WHERE capture_id=job.id;
 bytes:=bytes+octet_length(context::text)+(SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM public.export_resources r WHERE capture_id=job.id);
 IF bytes>268435456 OR bytes+(SELECT COALESCE(sum(capture_bytes),0) FROM public.execution_exports e WHERE e.scope_key=s AND e.caller_id=job.caller_id AND e.id<>job.id)>536870912
 THEN RAISE EXCEPTION 'export_capacity_exceeded'; END IF;
 UPDATE public.execution_exports SET capture_bytes=bytes WHERE id=job.id;
 RETURN context||jsonb_build_object('expires_at',job.expires_at);
END $$;
"""

SOURCE_CHARTS = r"""
CREATE FUNCTION public.opencitadel_export_source_charts(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;capture uuid;selected uuid[];runs jsonb;tools jsonb; saved record;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb;s:=p->>'scope';SELECT id INTO capture FROM public.comparison_revisions WHERE scope_key=s AND comparison_id=(p->>'comparison_id')::uuid AND revision=(p->>'revision')::integer;
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
 ELSIF p->>'operation'='read' THEN
  SELECT * INTO saved FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s AND (c.published OR EXISTS(SELECT 1 FROM public.export_staging t WHERE t.capture_id=c.id AND t.transaction_id=pg_current_xact_id() AND t.caller_id=current_setting('app.user_id',true)));
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
