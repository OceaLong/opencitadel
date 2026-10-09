"""Durable comparisons: immutable safe facts, fixed resources and authored revisions.

A01-derived arithmetic is copied here at this forward revision, never imported from
mutable application code. No runtime role gains raw retained-fact access.
"""

import sqlalchemy as sa

from alembic import op

revision = "0017execution_comparisons"
down_revision = "0016execution_analysis"
branch_labels = None
depends_on = None

ACCOUNTING = r"""
CREATE FUNCTION public.opencitadel_comparison_accounting(encoded text, signature text, capture uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; mode text; batch text; n integer; saved jsonb;
BEGIN
 actor:=current_setting('app.user_id',true);
 SELECT scope_key,query INTO s,saved FROM public.comparison_revisions WHERE id=capture AND caller_id=actor;
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 SELECT item->>1 INTO mode FROM jsonb_array_elements(saved->'filters') item WHERE item->>0='accounting';
 SELECT item->>1 INTO batch FROM jsonb_array_elements(saved->'filters') item WHERE item->>0='batch_id';
 mode:=COALESCE(mode,'run');
 IF mode NOT IN ('run','selected_result','batch_total') OR (mode='batch_total' AND batch IS NULL) THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 WITH selected_results AS(SELECT DISTINCT r.id,r.batch_id FROM public.evaluation_batch_results r WHERE r.scope_key=s AND
 ((mode='batch_total' AND r.batch_id::text=batch) OR (mode='selected_result' AND EXISTS(SELECT 1 FROM public.evaluation_batch_attempts a JOIN public.comparison_members m ON m.scope_key=s AND m.run_id=a.run_id AND m.capture_id=capture WHERE a.scope_key=s AND a.result_id=r.id)))),
 linked AS(SELECT run_id FROM public.comparison_members WHERE scope_key=s AND capture_id=capture AND mode='run'
 UNION SELECT a.run_id FROM selected_results r JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.result_id=r.id
 UNION SELECT j.run_id FROM selected_results r JOIN public.evaluation_judge_intents j ON j.scope_key=s AND j.result_id=r.id),
 bounded AS(SELECT run_id FROM linked ORDER BY run_id LIMIT 1000001),
 inserted AS(INSERT INTO public.comparison_accounting(capture_id,scope_key,run_id,formal_position,progress_position,observed_order,projector_version,generation,ordinal)
 SELECT capture,s,b.run_id,COALESCE(r.formal_position,0),COALESCE(r.progress_position,0),COALESCE(r.observed_order,0),COALESCE(r.projector_version,1),
 COALESCE((SELECT active_generation::text FROM public.execution_view_controls WHERE scope_key=s),'live'),row_number() OVER(ORDER BY b.run_id)-1
 FROM bounded b LEFT JOIN public.execution_view_runs r ON r.scope_key=s AND r.run_id=b.run_id RETURNING run_id)
 SELECT count(*) INTO n FROM inserted;
 IF n>1000000 THEN RAISE EXCEPTION 'analysis_accounting_capacity_exceeded'; END IF;
 RETURN n;
END $$
"""

PRIVATE_FACTS = r"""
CREATE FUNCTION public.opencitadel_comparison_usage(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; result jsonb; rev bigint;
BEGIN
 actor:=current_setting('app.user_id',true);
 SELECT scope_key INTO s FROM public.comparison_revisions WHERE id=capture AND caller_id=actor;
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 WITH selected_runs AS (SELECT run_id,formal_position FROM public.comparison_accounting WHERE capture_id=capture AND scope_key=s),
 physical AS (SELECT d.run_id,d.call_identity,c.purpose,
 CASE WHEN settled.event_position<=m.formal_position THEN f.fact ELSE NULL END AS fact
 FROM selected_runs m JOIN public.execution_model_dispatches d ON d.scope_key=s AND d.run_id=m.run_id
 JOIN public.execution_configurations c ON c.scope_key=s AND c.id=d.configuration_id
 JOIN public.execution_usage_publications p ON p.scope_key=s AND p.call_identity=d.call_identity AND p.phase='dispatch' AND p.event_position<=m.formal_position
 LEFT JOIN public.execution_usage_publications settled ON settled.scope_key=s AND settled.call_identity=d.call_identity AND settled.phase='settlement'
 LEFT JOIN public.execution_model_settlements f ON f.scope_key=s AND f.call_identity=d.call_identity),
 body AS(SELECT jsonb_build_object('run_id',run_id,'call_identity',call_identity,'purpose',purpose,
 'input_tokens',fact->'usage'->'prompt_tokens','output_tokens',fact->'usage'->'completion_tokens','cost_usd',fact->'cost_usd') AS item FROM physical)
 SELECT COALESCE(jsonb_agg(item),'[]'::jsonb) INTO result FROM body;
 RETURN result;
END $$
"""

SCORE_FACTS = r"""
CREATE FUNCTION public.opencitadel_comparison_scores(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; result jsonb; rev bigint;
BEGIN
 actor:=current_setting('app.user_id',true);
 SELECT scope_key INTO s FROM public.comparison_revisions WHERE id=capture AND caller_id=actor;
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 WITH members AS (SELECT DISTINCT r.id AS result_id,r.case_revision_id AS case_id,r.config_version_id AS config_id,r.execution_status,r.batch_id,a.run_id,
 v.family,t.dataset_version,t.body->>'mode' AS mode,COALESCE(t.body->>'environment_version','recorded') AS environment_version,t.rubric_version,
 COALESCE(h.revision,0) AS evaluation_revision
 FROM public.comparison_members m JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.run_id=m.run_id
 JOIN public.evaluation_batch_results r ON r.scope_key=s AND r.id=a.result_id AND r.attempt=a.attempt
 JOIN public.evaluation_batches b ON b.scope_key=s AND b.id=r.batch_id
 JOIN public.evaluation_suite_versions t ON t.scope_key=s AND t.id=b.suite_version
 JOIN public.execution_view_runs v ON v.scope_key=s AND v.run_id=a.run_id
 LEFT JOIN public.evaluation_score_heads h ON h.scope_key=s AND h.batch_id=b.id
 WHERE m.capture_id=capture AND m.scope_key=s),
 rubrics AS(SELECT result_id,rubric_version AS rubric FROM members UNION SELECT m.result_id,t.rubric_revision FROM members m JOIN public.evaluation_score_sets t ON t.scope_key=s AND t.result_id=m.result_id AND t.evaluation_revision<=m.evaluation_revision),
 sets AS(SELECT DISTINCT ON(t.result_id,t.source,t.rubric_revision) t.* FROM members m JOIN public.evaluation_score_sets t ON t.scope_key=s AND t.result_id=m.result_id AND t.run_id=m.run_id AND t.evaluation_revision<=m.evaluation_revision ORDER BY t.result_id,t.source,t.rubric_revision,t.evaluation_revision DESC),
 heads AS(SELECT DISTINCT ON(t.result_id,t.source,t.rubric_revision,q.dimension) t.result_id,t.source,t.rubric_revision,q.dimension,q.value,q.status,t.id AS source_set_id,
 EXISTS(SELECT 1 FROM public.evaluation_judge_invalidations i WHERE i.scope_key=s AND i.batch_id=m.batch_id AND i.source_set_id=t.id AND i.evaluation_revision<=m.evaluation_revision) AS invalidated
 FROM members m JOIN public.evaluation_score_sets t ON t.scope_key=s AND t.result_id=m.result_id AND t.run_id=m.run_id AND t.evaluation_revision<=m.evaluation_revision
 JOIN public.evaluation_scores q ON q.scope_key=s AND q.set_id=t.id
 ORDER BY t.result_id,t.source,t.rubric_revision,q.dimension,t.evaluation_revision DESC)
 SELECT COALESCE(jsonb_agg(jsonb_build_object('run_id',m.run_id,'result_id',m.result_id,'case_id',m.case_id,'config_id',m.config_id,'execution_status',m.execution_status,
 'batch_id',m.batch_id,'family',m.family,'dataset_version',m.dataset_version,'mode',m.mode,'environment_version',m.environment_version,'rubric',r.rubric,'evaluation_revision',m.evaluation_revision,
 'required_conditions',COALESCE(v.body->'required_conditions','[]'::jsonb),
 'source_sets',COALESCE((SELECT jsonb_agg(jsonb_build_object('source',t.source,'required_dimensions',t.required_dimensions,'applicable_dimensions',t.applicable_dimensions,'source_set_id',t.id)) FROM sets t WHERE t.result_id=m.result_id AND t.rubric_revision=r.rubric),'[]'::jsonb),
 'scores',COALESCE((SELECT jsonb_agg(jsonb_build_object('source',h.source,'dimension',h.dimension,'value',h.value,'status',h.status,'invalidated',h.invalidated,'source_set_id',h.source_set_id)) FROM heads h WHERE h.result_id=m.result_id AND h.rubric_revision=r.rubric),'[]'::jsonb))),'[]'::jsonb)
 INTO result FROM members m JOIN rubrics r ON r.result_id=m.result_id JOIN public.evaluation_rubric_versions v ON v.scope_key=s AND v.id=r.rubric;
 RETURN result;
END $$
"""

ALLOCATIONS = r"""
CREATE FUNCTION public.opencitadel_comparison_allocations(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; saved jsonb; mode text; batch text; result jsonb;
BEGIN
 SELECT scope_key,query INTO s,saved FROM public.comparison_revisions WHERE id=capture AND caller_id=current_setting('app.user_id',true);
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 SELECT f->>1 INTO mode FROM jsonb_array_elements(saved->'filters') f WHERE f->>0='accounting';
 IF mode IS NULL OR mode='run' THEN RETURN '[]'::jsonb; END IF;
 SELECT f->>1 INTO batch FROM jsonb_array_elements(saved->'filters') f WHERE f->>0='batch_id';
 WITH batches AS(SELECT b.id,b.principal FROM public.evaluation_batches b WHERE b.scope_key=s AND
 ((mode='batch_total' AND b.id::text=batch) OR (mode='selected_result' AND EXISTS(SELECT 1 FROM public.comparison_members m JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.run_id=m.run_id JOIN public.evaluation_batch_results r ON r.scope_key=s AND r.id=a.result_id WHERE m.scope_key=s AND m.capture_id=capture AND r.batch_id=b.id)))),
 allocations AS(SELECT DISTINCT n.id,b.id AS batch_id,CASE WHEN n.id=b.id THEN 'original' ELSE 'additional' END AS kind,
 n.body->'token_budget' AS token_budget,n.body->'money_budget' AS money_budget,
 CASE WHEN n.id=b.id THEN b.principal->>'user_id' ELSE j.authorizer->>'user_id' END AS authorizer,j.id AS intent_id
 FROM batches b JOIN public.evaluation_budget_namespaces n ON n.scope_key=s
 LEFT JOIN public.evaluation_judge_intents j ON j.scope_key=s AND j.namespace_id=n.id AND j.batch_id=b.id AND j.rescore IS NOT NULL
 WHERE n.id=b.id OR j.id IS NOT NULL)
 SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.batch_id,a.kind,a.id),'[]'::jsonb) INTO result FROM allocations a;
 RETURN result;
END $$
"""

INTERVAL_FACTS = r"""WITH attempts AS (
 SELECT s.run_id,s.attempt_id,s.kind,s.status,s.business_outcome,
 greatest(s.started_at,r.admitted_at) AS left_at,least(s.ended_at,captured_at_value,r.terminal_at) AS right_at,
 s.started_at IS NOT NULL AND s.ended_at IS NOT NULL AND s.ended_at>=s.started_at AS valid
 FROM public.comparison_members m JOIN public.execution_view_runs r ON r.scope_key=m.scope_key AND r.run_id=m.run_id
 JOIN public.execution_view_steps s ON s.scope_key=m.scope_key AND s.run_id=m.run_id
 WHERE m.capture_id=capture_id_value AND m.scope_key=scope_value AND s.attempt_id IS NOT NULL AND s.status<>'queued'),
 previous AS (SELECT *,max(right_at) OVER(PARTITION BY run_id ORDER BY left_at,right_at ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end FROM attempts WHERE valid AND right_at>=left_at),
 occupancy AS (SELECT run_id,sum(extract(epoch FROM right_at-greatest(left_at,COALESCE(previous_end,left_at)))*1000) FILTER(WHERE previous_end IS NULL OR right_at>previous_end) AS occupancy FROM previous GROUP BY run_id)
SELECT a.run_id,max(o.occupancy) AS activity_occupancy_ms,
 count(*) FILTER(WHERE valid) AS activity_samples,count(*) FILTER(WHERE NOT valid) AS activity_missing,
 sum(extract(epoch FROM right_at-left_at)*1000) FILTER(WHERE kind='tool' AND valid AND right_at>=left_at) AS tool_work_ms,
 count(*) FILTER(WHERE kind='tool' AND valid) AS tool_samples,count(*) FILTER(WHERE kind='tool' AND NOT valid) AS tool_missing,
 count(*) FILTER(WHERE kind='tool' AND status IN ('completed','failed')) AS tool_terminal,
 count(*) FILTER(WHERE kind='tool' AND status IN ('completed','failed') AND (status='failed' OR business_outcome IN ('failure','failed'))) AS tool_errors,
 count(*) FILTER(WHERE kind='tool' AND status NOT IN ('completed','failed')) AS tool_excluded,
 count(*) FILTER(WHERE kind='tool' AND status='failed') AS tool_execution_errors,
 count(*) FILTER(WHERE kind='tool' AND status IN ('completed','failed') AND business_outcome IN ('failed','failure')) AS tool_business_errors,
 count(*) FILTER(WHERE kind='tool' AND status='unknown') AS tool_unknown,
 count(*) FILTER(WHERE kind='tool' AND status='deferred') AS tool_deferred,
 count(*) FILTER(WHERE kind='tool' AND status='cancelled') AS tool_cancelled
FROM attempts a LEFT JOIN occupancy o ON o.run_id=a.run_id GROUP BY a.run_id

"""

APPROVAL_FACTS = r"""WITH transitions AS (
 SELECT m.run_id,f->>'id' AS approval_id,o.occurred_at,f->'patch'->>'status' AS status
 FROM public.comparison_members m JOIN public.execution_view_observations o ON o.scope_key=m.scope_key AND o.run_id=m.run_id
 CROSS JOIN LATERAL jsonb_array_elements(o.public_payload->'facts') f
 WHERE m.capture_id=capture_id_value AND m.scope_key=scope_value AND o.projector_version=m.projector_version
 AND o.observed_order<=m.observed_order AND o.formal_position<=m.formal_position AND o.progress_position<=m.progress_position
 AND COALESCE((o.public_payload->>'applied')::boolean,true) AND f->>'kind'='approval'),
 raw_intervals AS (SELECT run_id,approval_id,min(occurred_at) FILTER(WHERE status IN ('waiting','requested','pending')) AS left_at,
 COALESCE(min(occurred_at) FILTER(WHERE status IN ('decided','expired','approved','rejected','cancelled')),captured_at_value) AS right_at FROM transitions GROUP BY run_id,approval_id),
 intervals AS (SELECT i.run_id,i.approval_id,CASE WHEN i.left_at IS NOT NULL THEN greatest(i.left_at,r.admitted_at) END AS left_at,least(i.right_at,captured_at_value,COALESCE(r.terminal_at,captured_at_value)) AS right_at FROM raw_intervals i JOIN public.execution_view_runs r ON r.scope_key=scope_value AND r.run_id=i.run_id),
 previous AS (SELECT *,max(right_at) OVER(PARTITION BY run_id ORDER BY left_at,right_at ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end FROM intervals WHERE left_at IS NOT NULL AND right_at>=left_at),
 totals AS (SELECT run_id,sum(extract(epoch FROM right_at-greatest(left_at,COALESCE(previous_end,left_at)))*1000) FILTER(WHERE previous_end IS NULL OR right_at>previous_end) AS duration FROM previous GROUP BY run_id)
SELECT i.run_id,max(t.duration) AS approval_wait_ms,count(*) FILTER(WHERE left_at IS NOT NULL AND right_at>=left_at) AS samples,
 count(*) FILTER(WHERE left_at IS NULL OR right_at<left_at) AS missing FROM intervals i LEFT JOIN totals t ON t.run_id=i.run_id GROUP BY i.run_id

"""

SELECTION = r"""SELECT r.run_id,r.formal_position,r.progress_position,r.observed_order,r.projection_revision,
 r.projector_version,r.as_of,r.completeness,admission.id AS admission_configuration_id,
 COALESCE((SELECT c.active_generation::text FROM public.execution_view_controls c WHERE c.scope_key=r.scope_key),'live') AS generation
FROM public.execution_view_runs r LEFT JOIN LATERAL(SELECT c.id,c.purpose FROM public.execution_configurations c WHERE c.scope_key=r.scope_key AND c.run_id=r.run_id AND c.body->>'stage'='admission' ORDER BY c.created_at,c.id LIMIT 1) admission ON true
WHERE r.scope_key=scope_value AND r.admitted_at>=start_at AND r.admitted_at<end_at AND r.observed_order>0
AND (q_family IS NULL OR r.family=q_family)
AND (q_status IS NULL OR r.status=q_status)
AND (q_purpose IS NULL OR r.purpose=q_purpose)
AND (q_mode IS NULL OR r.execution_mode=q_mode)
AND (q_configuration_revision IS NULL OR admission.id=q_configuration_revision)
AND (q_model_revision IS NULL OR EXISTS(SELECT 1 FROM public.execution_model_dispatches d JOIN public.execution_model_settlements f ON f.scope_key=d.scope_key AND f.call_identity=d.call_identity JOIN public.execution_usage_publications p ON p.scope_key=d.scope_key AND p.call_identity=d.call_identity AND p.phase='settlement' AND p.event_position<=r.formal_position WHERE d.scope_key=r.scope_key AND d.run_id=r.run_id AND f.fact->>'model_revision'=q_model_revision))
AND (q_session IS NULL OR r.source->>'entity_type'='session' AND r.source->>'entity_id'=q_session)
AND (q_tool IS NULL OR EXISTS(SELECT 1 FROM public.execution_view_steps s WHERE s.scope_key=r.scope_key AND s.run_id=r.run_id AND s.tool_name=q_tool))
AND (q_batch_id IS NULL OR EXISTS(SELECT 1 FROM public.evaluation_batch_attempts a JOIN public.evaluation_batch_results e ON e.scope_key=a.scope_key AND e.id=a.result_id WHERE a.scope_key=r.scope_key AND a.run_id=r.run_id AND e.batch_id::text=q_batch_id))
AND (r.source->>'entity_type' IS DISTINCT FROM 'session' OR EXISTS(SELECT 1 FROM public.sessions s WHERE s.id=r.source->>'entity_id' AND s.deleted_at IS NULL AND s.owner_user_id IS NOT DISTINCT FROM owner_value AND s.team_id IS NOT DISTINCT FROM team_value))
AND (selection_mode='all_matching' OR r.run_id IN (SELECT value::uuid FROM jsonb_array_elements_text(p->'run_ids')))
AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(p->'excluded_run_ids') excluded WHERE excluded::uuid=r.run_id)
ORDER BY r.admitted_at,r.run_id LIMIT 100001"""

RESOURCE_BINDINGS = r"""WITH members AS (SELECT * FROM jsonb_to_recordset(CAST(members_json AS jsonb)) AS m(run_id uuid,formal_position bigint,progress_position bigint,observed_order bigint,projector_version integer,generation text)),
runs AS (SELECT m.*,r.source,r.completeness,r.scope_key,
 r.run_id IS NOT NULL AND (r.source->>'entity_type' IS DISTINCT FROM 'session' OR EXISTS(SELECT 1 FROM public.sessions s WHERE s.id=r.source->>'entity_id' AND s.deleted_at IS NULL AND s.owner_user_id IS NOT DISTINCT FROM owner_value AND s.team_id IS NOT DISTINCT FROM team_value)) AS visible
 FROM members m LEFT JOIN LATERAL (
 SELECT actual.* FROM public.execution_view_runs actual
 -- Keep each member lookup indexed when a fresh scope's row count is underestimated.
 WHERE actual.run_id=m.run_id AND actual.scope_key=scope_value OFFSET 0
 ) r ON true),
version_owners AS (
 SELECT DISTINCT m.run_id,'dataset_version'::text AS kind,s.dataset_version::text AS id
 FROM members m JOIN public.evaluation_batch_attempts a ON a.scope_key=scope_value AND a.run_id=m.run_id
 JOIN public.evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id
 JOIN public.evaluation_batches b ON b.scope_key=r.scope_key AND b.id=r.batch_id
 JOIN public.evaluation_suite_versions s ON s.scope_key=b.scope_key AND s.id=b.suite_version
 UNION SELECT DISTINCT m.run_id,'config_version',r.config_version_id::text
 FROM members m JOIN public.evaluation_batch_attempts a ON a.scope_key=scope_value AND a.run_id=m.run_id
 JOIN public.evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id
 UNION SELECT DISTINCT m.run_id,'recording_version',v
 FROM members m JOIN public.evaluation_batch_attempts a ON a.scope_key=scope_value AND a.run_id=m.run_id
 JOIN public.evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id
 JOIN public.evaluation_batches b ON b.scope_key=r.scope_key AND b.id=r.batch_id
 JOIN public.evaluation_suite_versions s ON s.scope_key=b.scope_key AND s.id=b.suite_version
 CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(s.body->'recording_versions','[]'::jsonb)) v),
fixed_refs AS (
 SELECT o.run_id,o.kind AS owner_kind,o.id AS owner_id,e->>'resource_kind' AS kind,e->>'resource_id' AS id,e->>'resource_version' AS version
 FROM version_owners o JOIN public.evaluation_config_versions c ON o.kind='config_version' AND c.scope_key=scope_value AND c.id::text=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(c.body->'selection'->'resources','[]'::jsonb)) e
 UNION SELECT o.run_id,o.kind,o.id,e->>'resource_kind',e->>'resource_id',e->>'resource_version'
 FROM version_owners o JOIN public.evaluation_recording_versions v ON o.kind='recording_version' AND v.scope_key=scope_value AND v.id::text=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(v.body->'pins','[]'::jsonb)) e),
refs AS (
 SELECT f.run_id,f.kind,f.id,f.version,EXISTS(SELECT 1 FROM public.resource_pins p WHERE p.scope_key=scope_value AND p.owner_kind=f.owner_kind AND p.owner_id=f.owner_id AND p.resource_kind=f.kind AND p.resource_id=f.id AND p.resource_version=f.version AND p.available) AS pinned FROM fixed_refs f
 UNION SELECT run_id,kind,id,version,true AS pinned FROM jsonb_to_recordset(CAST(resources_json AS jsonb)) AS j(run_id uuid,kind text,id text,version text)
 UNION SELECT m.run_id,e->>'resource_kind',e->>'resource_id',e->>'resource_version',true
 FROM members m JOIN public.evaluation_batch_attempts a ON a.scope_key=scope_value AND a.run_id=m.run_id
 JOIN public.evaluation_score_sets v ON v.scope_key=scope_value AND v.result_id=a.result_id
 JOIN public.evaluation_scores q ON q.scope_key=v.scope_key AND q.set_id=v.id
 CROSS JOIN LATERAL jsonb_array_elements(q.evidence) e
 WHERE EXISTS(SELECT 1 FROM public.comparison_scores saved CROSS JOIN LATERAL jsonb_array_elements(saved.body->'scores') head WHERE saved.capture_id=capture_id_value AND saved.run_id=m.run_id AND head->>'source_set_id'=v.id::text)
 UNION SELECT r.run_id,p.resource_kind AS kind,p.resource_id AS id,p.resource_version AS version,EXISTS(SELECT 1 FROM public.resource_pins live WHERE live.scope_key=p.scope_key AND live.owner_kind=p.owner_kind AND live.owner_id=p.owner_id AND live.resource_kind=p.resource_kind AND live.resource_id=p.resource_id AND live.resource_version=p.resource_version AND live.available) AS pinned
 FROM runs r JOIN public.analysis_required_pins p ON p.scope_key=scope_value AND (
 (p.owner_kind='run' AND p.owner_id=r.run_id::text) OR
 (p.owner_kind='session' AND r.source->>'entity_type'='session' AND p.owner_id=r.source->>'entity_id') OR
 EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND o.kind=p.owner_kind AND o.id=p.owner_id))
 UNION SELECT m.run_id,'execution_content',c.content_id::text,c.content_digest,true
 FROM members m JOIN public.execution_content_bindings b ON b.scope_key=scope_value AND b.run_id=m.run_id AND b.formal_position<=m.formal_position
 JOIN public.execution_public_content c ON c.scope_key=b.scope_key AND c.content_id=b.content_id),
resources_by_run AS (
 SELECT f.run_id,jsonb_agg(DISTINCT jsonb_build_object('resource_kind',f.kind,'resource_id',f.id,'resource_version',f.version)) AS resources
 FROM refs f GROUP BY f.run_id),
pins_by_run AS (
 SELECT r.run_id,jsonb_agg(DISTINCT jsonb_build_object('owner_kind',p.owner_kind,'owner_id',p.owner_id,'resource_kind',p.resource_kind,'resource_id',p.resource_id,'resource_version',p.resource_version)) AS pins
 FROM runs r JOIN public.analysis_required_pins p ON p.scope_key=scope_value AND ((p.owner_kind='run' AND p.owner_id=r.run_id::text) OR (p.owner_kind='session' AND r.source->>'entity_type'='session' AND p.owner_id=r.source->>'entity_id') OR EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND o.kind=p.owner_kind AND o.id=p.owner_id))
 GROUP BY r.run_id),
owners_by_run AS (
 SELECT o.run_id,jsonb_agg(jsonb_build_object('kind',o.kind,'id',o.id)) AS owners
 FROM version_owners o GROUP BY o.run_id)
SELECT m.run_id, r.source,
 COALESCE(resources_by_run.resources,'[]'::jsonb) AS resources,
 COALESCE(pins_by_run.pins,'[]'::jsonb) AS pins,
 COALESCE(owners_by_run.owners,'[]'::jsonb) AS owners
FROM members m JOIN LATERAL (
 SELECT actual.* FROM public.execution_view_runs actual
 -- Keep each member lookup indexed when a fresh scope's row count is underestimated.
 WHERE actual.run_id=m.run_id AND actual.scope_key=scope_value OFFSET 0
 ) r ON true
 LEFT JOIN resources_by_run ON resources_by_run.run_id=m.run_id
 LEFT JOIN pins_by_run ON pins_by_run.run_id=m.run_id
 LEFT JOIN owners_by_run ON owners_by_run.run_id=m.run_id
"""

TABLES = {
    "comparison_receipts": """scope_key text NOT NULL,caller_id text NOT NULL,request_id text NOT NULL CHECK(length(request_id) BETWEEN 1 AND 128),fingerprint text NOT NULL,response jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(scope_key,caller_id,request_id)""",
    "comparison_sets": """id uuid PRIMARY KEY,scope_key text NOT NULL,caller_id text NOT NULL,revision integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(scope_key,id)""",
    "comparison_revisions": """id uuid PRIMARY KEY,comparison_id uuid NOT NULL,scope_key text NOT NULL,caller_id text NOT NULL,revision integer NOT NULL,query jsonb NOT NULL,selection jsonb NOT NULL,baseline_configuration text,authority_revision bigint NOT NULL,captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),metric_version text NOT NULL,alignment_revision integer NOT NULL DEFAULT 0,published boolean NOT NULL DEFAULT false,UNIQUE(scope_key,id),UNIQUE(comparison_id,revision),FOREIGN KEY(scope_key,comparison_id) REFERENCES comparison_sets(scope_key,id)""",
    "comparison_members": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<100000),formal_position bigint NOT NULL,progress_position bigint NOT NULL,observed_order bigint NOT NULL,projection_revision bigint NOT NULL,projector_version integer NOT NULL,generation text NOT NULL,coverage jsonb NOT NULL,run_fact jsonb NOT NULL DEFAULT '{}',interval_fact jsonb,approval_fact jsonb,PRIMARY KEY(capture_id,run_id),UNIQUE(capture_id,ordinal),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_accounting": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<=1000000),formal_position bigint NOT NULL,progress_position bigint NOT NULL,observed_order bigint NOT NULL,projector_version integer NOT NULL,generation text NOT NULL,PRIMARY KEY(capture_id,run_id),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_usage": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,call_identity text NOT NULL,body jsonb NOT NULL,PRIMARY KEY(capture_id,call_identity),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_scores": """id uuid PRIMARY KEY DEFAULT gen_random_uuid(),capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,body jsonb NOT NULL,FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_accounting_links": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,result_id uuid NOT NULL,PRIMARY KEY(capture_id,run_id,result_id),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_resources": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,source jsonb,resources jsonb NOT NULL,pins jsonb NOT NULL,owners jsonb NOT NULL,PRIMARY KEY(capture_id,run_id),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_details": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,slot integer NOT NULL CHECK(slot>=0 AND slot<5),body jsonb NOT NULL CHECK(octet_length(body::text)<=33554432),PRIMARY KEY(capture_id,run_id),UNIQUE(capture_id,slot),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_alignment_pairs": """capture_id uuid NOT NULL,scope_key text NOT NULL,pair_key text NOT NULL,left_run_id uuid NOT NULL,left_step_id text NOT NULL,left_attempt_id text NOT NULL DEFAULT '',right_run_id uuid NOT NULL,right_step_id text NOT NULL,right_attempt_id text NOT NULL DEFAULT '',revision integer NOT NULL,supersedes integer NOT NULL,author text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),body jsonb NOT NULL,PRIMARY KEY(capture_id,pair_key,left_run_id,left_step_id,left_attempt_id),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_alignments": """id uuid PRIMARY KEY DEFAULT gen_random_uuid(),capture_id uuid NOT NULL,scope_key text NOT NULL,revision integer NOT NULL,supersedes integer NOT NULL,author text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),body jsonb NOT NULL,PRIMARY KEY(id),UNIQUE(capture_id,revision),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""".replace(
        "id uuid PRIMARY KEY DEFAULT", "id uuid DEFAULT"
    ),
    "comparison_artifacts": """capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,step_id text NOT NULL,artifact_id text NOT NULL,version integer NOT NULL,provenance_id uuid NOT NULL,content_digest text NOT NULL,storage_key text NOT NULL,PRIMARY KEY(capture_id,run_id,step_id,artifact_id,version),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_allocations": """capture_id uuid NOT NULL,scope_key text NOT NULL,id uuid NOT NULL,body jsonb NOT NULL,PRIMARY KEY(capture_id,id),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_diff_jobs": """id uuid PRIMARY KEY,scope_key text NOT NULL,capture_id uuid NOT NULL,caller_id text NOT NULL,principal jsonb NOT NULL,owner_scope jsonb NOT NULL,selection jsonb NOT NULL,status text NOT NULL CHECK(status IN ('queued','running','complete','partial','failed')),lease_token uuid,lease_until timestamptz,body jsonb,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),FOREIGN KEY(scope_key,capture_id) REFERENCES comparison_revisions(scope_key,id)""",
    "comparison_diff_pages": """job_id uuid NOT NULL,scope_key text NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<16),body text NOT NULL CHECK(octet_length(body)<=65536),PRIMARY KEY(job_id,ordinal),FOREIGN KEY(job_id) REFERENCES comparison_diff_jobs(id)""",
}

RECEIPT = r"""
CREATE FUNCTION public.opencitadel_comparison_receipt(p jsonb,operation_name text,target jsonb,response_data jsonb DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text;actor text;identity text;fingerprint_value text;prior record;intent jsonb;
BEGIN
 s:=p->>'scope';actor:=current_setting('app.user_id',true);identity:=p->>'request_id';
 IF identity IS NULL OR length(identity) NOT BETWEEN 1 AND 128 OR identity<>btrim(identity) THEN RAISE EXCEPTION 'invalid_comparison_request_id'; END IF;
 intent:=COALESCE(p->'request_fingerprint',p-'principal'-'scope'-'expires'-'authorization_signature'-'request_id');
 fingerprint_value:=encode(public.digest(jsonb_build_object('operation',operation_name,'target',target,'intent',intent)::text,'sha256'),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended('comparison-request:'||s||':'||actor||':'||identity,0));
 SELECT r.* INTO prior FROM public.comparison_receipts r WHERE r.scope_key=s AND r.caller_id=actor AND r.request_id=identity;
 IF FOUND THEN
  IF prior.fingerprint<>fingerprint_value THEN RAISE EXCEPTION 'comparison_request_conflict'; END IF;
  IF prior.response='null'::jsonb THEN
   IF response_data IS NOT NULL THEN UPDATE public.comparison_receipts r SET response=response_data WHERE r.scope_key=s AND r.caller_id=actor AND r.request_id=identity; END IF;
   RETURN response_data;
  END IF;
  RETURN prior.response;
 END IF;
 INSERT INTO public.comparison_receipts(scope_key,caller_id,request_id,fingerprint,response) VALUES(s,actor,identity,fingerprint_value,COALESCE(response_data,'null'::jsonb)) ON CONFLICT DO NOTHING;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='comparison_receipt_snapshot_retry'; END IF;
 RETURN response_data;
END $$
"""

RESOURCE_CURRENT = r"""
CREATE FUNCTION public.opencitadel_comparison_resource_rows(scope_value text,capture uuid,selected uuid[] DEFAULT NULL)
RETURNS TABLE(run_id uuid,available boolean) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE owner_value text;team_value text;
BEGIN
 team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END;
 RETURN QUERY WITH bindings AS MATERIALIZED(
  SELECT m.run_id,m.resources FROM public.comparison_resources m WHERE selected IS NULL AND m.scope_key=scope_value AND m.capture_id=capture
  UNION ALL SELECT m.run_id,m.resources FROM (SELECT DISTINCT unnest(selected) AS run_id) target JOIN public.comparison_resources m ON m.scope_key=scope_value AND m.capture_id=capture AND m.run_id=target.run_id),
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
CREATE FUNCTION public.opencitadel_comparison_current_rows(scope_value text,capture uuid,selected uuid[] DEFAULT NULL)
RETURNS TABLE(run_id uuid,available boolean) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; team text; owner_value text;
BEGIN
 actor:=current_setting('app.user_id',true);team:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team IS NULL THEN actor ELSE NULL END;
 s:=scope_value;
 IF NOT EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=capture AND c.scope_key=s) THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 RETURN QUERY
 SELECT m.run_id,
 EXISTS(SELECT 1 FROM public.execution_view_runs r WHERE r.scope_key=s AND r.run_id=m.run_id)
 AND (m.source->>'entity_type' IS DISTINCT FROM 'session' OR EXISTS(SELECT 1 FROM public.sessions t WHERE t.id=m.source->>'entity_id' AND t.deleted_at IS NULL AND t.owner_user_id IS NOT DISTINCT FROM owner_value AND t.team_id IS NOT DISTINCT FROM team))
 AND resource_state.available
 AND NOT EXISTS(SELECT 1 FROM public.comparison_artifacts a LEFT JOIN public.artifact_version_provenance provenance ON provenance.id=a.provenance_id AND provenance.scope_key=s AND provenance.availability='available' AND provenance.binding_status='bound' AND provenance.content_digest=a.content_digest WHERE a.capture_id=capture AND a.run_id=m.run_id AND provenance.id IS NULL)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.pins) required WHERE NOT EXISTS(
 SELECT 1 FROM public.resource_pins p WHERE p.scope_key=s AND p.owner_kind=required->>'owner_kind' AND p.owner_id=required->>'owner_id' AND p.resource_kind=required->>'resource_kind' AND p.resource_id=required->>'resource_id' AND p.resource_version=required->>'resource_version' AND p.available))

 AND (NOT EXISTS(SELECT 1 FROM public.comparison_details d WHERE d.capture_id=capture AND d.run_id=m.run_id) OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.resources) resource WHERE NOT EXISTS(SELECT 1 FROM public.resource_pins pin WHERE pin.scope_key=s AND pin.owner_kind='comparison_revision' AND pin.owner_id=capture::text AND pin.resource_kind=resource->>'resource_kind' AND pin.resource_id=resource->>'resource_id' AND pin.resource_version=resource->>'resource_version' AND pin.available)))
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.owners) o WHERE
 CASE o->>'kind'
 WHEN 'dataset_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_dataset_versions d JOIN public.analysis_dataset_pin_coverage proof ON proof.scope_key=d.scope_key AND proof.version_id=d.id WHERE d.scope_key=s AND d.id::text=o->>'id')
 OR EXISTS(SELECT 1 FROM public.evaluation_version_cases vc JOIN public.evaluation_case_revisions cr ON cr.scope_key=vc.scope_key AND cr.id=vc.case_revision_id LEFT JOIN public.evaluation_object_intents ob ON ob.scope_key=cr.scope_key AND ob.id=cr.object_id WHERE vc.scope_key=s AND vc.version_id::text=o->>'id' AND (ob.id IS NULL OR ob.cleaned_at IS NOT NULL))
 WHEN 'recording_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_recording_versions rv WHERE rv.scope_key=s AND rv.id::text=o->>'id') OR EXISTS(SELECT 1 FROM public.evaluation_recording_slots rs LEFT JOIN public.evaluation_recording_objects ro ON ro.scope_key=rs.scope_key AND ro.id=rs.object_id WHERE rs.scope_key=s AND rs.version_id::text=o->>'id' AND (ro.id IS NULL OR ro.cleaned_at IS NOT NULL))
 WHEN 'config_version' THEN NOT EXISTS(SELECT 1 FROM public.evaluation_config_versions cv WHERE cv.scope_key=s AND cv.id::text=o->>'id')
 ELSE true END)
 FROM (SELECT r.* FROM public.comparison_resources r WHERE selected IS NULL AND r.capture_id=capture AND r.scope_key=s
 UNION ALL SELECT r.* FROM (SELECT DISTINCT unnest(selected) AS run_id) target JOIN public.comparison_resources r ON r.scope_key=s AND r.capture_id=capture AND r.run_id=target.run_id) m
 JOIN public.opencitadel_comparison_resource_rows(s,capture,selected) resource_state ON resource_state.run_id=m.run_id;
END $$
"""

CREATE_PREFIX = r"""
CREATE FUNCTION public.opencitadel_comparison_materialize(encoded text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p jsonb; scope_value text; actor text; team_value text; owner_value text; epoch bigint;
 comparison_id_value uuid; capture_id_value uuid; next_revision integer; query_value jsonb; selection_mode text;
 start_at timestamptz;end_at timestamptz;grain_value text;timezone_value text;captured_at_value timestamptz;
 q_family text;q_status text;q_purpose text;q_mode text;q_configuration_revision text;q_model_revision text;q_session text;q_tool text;q_batch_id text;
 filters jsonb; members_json jsonb; resources_json jsonb; section jsonb; checked jsonb; n integer;receipt_value jsonb;command_name text;command_target jsonb;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;
 IF p->'principal'->>'global_role'='auditor' THEN RAISE EXCEPTION 'comparison_read_only_principal'; END IF;
 actor:=current_setting('app.user_id',true);team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN actor ELSE NULL END;
 scope_value:=CASE WHEN team_value IS NULL THEN 'user:'||actor ELSE 'team:'||team_value END;
 IF p->>'operation' IS DISTINCT FROM 'materialize' THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 command_name:=CASE WHEN p->>'comparison_id' IS NULL THEN 'create' ELSE 'refresh' END;
 command_target:=jsonb_build_object('comparison_id',p->'comparison_id','expected_revision',p->'expected_revision');
 receipt_value:=public.opencitadel_comparison_receipt(p,command_name,command_target);
 IF receipt_value IS NOT NULL THEN
  IF NOT EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.scope_key=scope_value AND c.id=(receipt_value->>'revision_id')::uuid AND c.published) THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
  RETURN receipt_value||jsonb_build_object('replayed',true);
 END IF;
 query_value:=p->'query';start_at:=(query_value->>'start')::timestamptz;end_at:=(query_value->>'end')::timestamptz;
 grain_value:=query_value->>'grain';timezone_value:=query_value->>'timezone';selection_mode:=p->>'mode';
 IF start_at IS NULL OR end_at IS NULL OR end_at<=start_at OR end_at-start_at>interval '90 days' OR grain_value NOT IN ('hour','day')
 OR NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=timezone_value) OR jsonb_typeof(query_value->'filters') IS DISTINCT FROM 'array'
 OR selection_mode NOT IN ('explicit','all_matching') OR jsonb_typeof(p->'run_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'run_ids')>100000
 OR jsonb_typeof(p->'excluded_run_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'excluded_run_ids')>100000
 OR jsonb_typeof(p->'detail_run_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'detail_run_ids')>5
 THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(query_value->'filters') f WHERE jsonb_array_length(f)<>2 OR f->>0 NOT IN ('family','status','purpose','mode','configuration_revision','model_revision','session','tool','batch_id','accounting') OR jsonb_typeof(f->1)<>'string') THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT COALESCE(jsonb_object_agg(f->>0,f->1),'{}'::jsonb) INTO filters FROM jsonb_array_elements(query_value->'filters') f;
 q_family:=filters->>'family';q_status:=filters->>'status';q_purpose:=filters->>'purpose';q_mode:=filters->>'mode';q_configuration_revision:=filters->>'configuration_revision';q_model_revision:=filters->>'model_revision';q_session:=filters->>'session';q_tool:=filters->>'tool';q_batch_id:=filters->>'batch_id';
 IF p->>'comparison_id' IS NULL THEN
  comparison_id_value:=public.gen_random_uuid();next_revision:=1;
  INSERT INTO public.comparison_sets(id,scope_key,caller_id,revision) VALUES(comparison_id_value,scope_value,actor,next_revision);
 ELSE
  comparison_id_value:=(p->>'comparison_id')::uuid;
  UPDATE public.comparison_sets c SET revision=c.revision+1 WHERE c.id=comparison_id_value AND c.scope_key=scope_value AND c.revision=(p->>'expected_revision')::integer RETURNING revision INTO next_revision;
  IF NOT FOUND THEN RAISE EXCEPTION 'comparison_conflict'; END IF;
 END IF;
 capture_id_value:=public.gen_random_uuid();
 SELECT COALESCE(jsonb_agg(to_jsonb(r)),'[]'::jsonb) INTO members_json FROM (
"""
CREATE_AFTER_SELECTION = r"""
 ) r;
 IF jsonb_array_length(members_json)>100000 THEN RAISE EXCEPTION 'comparison_capacity_exceeded'; END IF;
 IF selection_mode='explicit' AND jsonb_array_length(members_json)<>(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(p->'run_ids')) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
 checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') r WHERE (r->>'available')::boolean IS DISTINCT FROM true) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
 INSERT INTO public.comparison_revisions(id,comparison_id,scope_key,caller_id,revision,query,selection,baseline_configuration,authority_revision,metric_version)
 VALUES(capture_id_value,comparison_id_value,scope_value,actor,next_revision,query_value,jsonb_build_object('mode',selection_mode,'run_ids',p->'run_ids','excluded_run_ids',p->'excluded_run_ids'),p->>'baseline_configuration',epoch,'execution-analysis-v1') RETURNING captured_at INTO captured_at_value;
 INSERT INTO public.comparison_members(capture_id,scope_key,run_id,ordinal,formal_position,progress_position,observed_order,projection_revision,projector_version,generation,coverage,run_fact)
 SELECT capture_id_value,scope_value,(m->>'run_id')::uuid,ordinality-1,(m->>'formal_position')::bigint,(m->>'progress_position')::bigint,(m->>'observed_order')::bigint,(m->>'projection_revision')::bigint,(m->>'projector_version')::integer,m->>'generation',m->'completeness',jsonb_build_object('admission_configuration_id',m->>'admission_configuration_id') FROM jsonb_array_elements(members_json) WITH ORDINALITY AS x(m,ordinality);
 PERFORM public.opencitadel_comparison_accounting(encoded,signature,capture_id_value);
 UPDATE public.comparison_members m SET run_fact=m.run_fact||jsonb_build_object('run_id',r.run_id,'family',r.family,'purpose',r.purpose,'execution_mode',r.execution_mode,'configuration_revision',q_configuration_revision,'status',r.status,'admitted_at',r.admitted_at,'terminal_at',r.terminal_at)
 FROM public.execution_view_runs r WHERE m.capture_id=capture_id_value AND m.scope_key=scope_value AND r.scope_key=m.scope_key AND r.run_id=m.run_id;
 INSERT INTO public.comparison_usage(capture_id,scope_key,run_id,call_identity,body)
 SELECT capture_id_value,scope_value,(v->>'run_id')::uuid,v->>'call_identity',v FROM jsonb_array_elements(public.opencitadel_comparison_usage(encoded,signature,capture_id_value)) v;
 INSERT INTO public.comparison_scores(capture_id,scope_key,run_id,body)
 SELECT capture_id_value,scope_value,(v->>'run_id')::uuid,v FROM jsonb_array_elements(public.opencitadel_comparison_scores(encoded,signature,capture_id_value)) v;
 INSERT INTO public.comparison_accounting_links(capture_id,scope_key,run_id,result_id)
 SELECT capture_id_value,scope_value,m.run_id,a.result_id FROM public.comparison_accounting m JOIN public.evaluation_batch_attempts a ON a.scope_key=m.scope_key AND a.run_id=m.run_id WHERE m.capture_id=capture_id_value
 UNION SELECT capture_id_value,scope_value,m.run_id,j.result_id FROM public.comparison_accounting m JOIN public.evaluation_judge_intents j ON j.scope_key=m.scope_key AND j.run_id=m.run_id WHERE m.capture_id=capture_id_value;
 UPDATE public.comparison_members m SET interval_fact=to_jsonb(f) FROM (
"""
CREATE_BETWEEN_FACTS = r"""
 ) f WHERE m.capture_id=capture_id_value AND m.scope_key=scope_value AND m.run_id=f.run_id;
 UPDATE public.comparison_members m SET approval_fact=to_jsonb(f) FROM (
"""
CREATE_BEFORE_BINDINGS = r"""
 ) f WHERE m.capture_id=capture_id_value AND m.scope_key=scope_value AND m.run_id=f.run_id;
 SELECT COALESCE(jsonb_agg(to_jsonb(m)),'[]'::jsonb) INTO members_json FROM (
 SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.comparison_members WHERE capture_id=capture_id_value
 UNION SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.comparison_accounting WHERE capture_id=capture_id_value) m;
 checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') r WHERE (r->>'available')::boolean IS DISTINCT FROM true) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
 resources_json:=public.opencitadel_analysis_judge_resources(encoded,signature,members_json);
 INSERT INTO public.comparison_resources(capture_id,scope_key,run_id,source,resources,pins,owners)
 SELECT capture_id_value,scope_value,f.run_id,f.source,f.resources,f.pins,f.owners FROM (
"""
CREATE_SUFFIX = r"""
 ) f;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(p->'detail_run_ids') requested WHERE NOT EXISTS(SELECT 1 FROM public.comparison_members m WHERE m.capture_id=capture_id_value AND m.run_id=requested::uuid)) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM public.execution_view_steps s WHERE s.scope_key=scope_value AND s.run_id IN(SELECT value::uuid FROM jsonb_array_elements_text(p->'detail_run_ids')) GROUP BY s.run_id HAVING count(*)>10000) THEN RAISE EXCEPTION 'comparison_step_limit'; END IF;
 INSERT INTO public.comparison_details(capture_id,scope_key,run_id,slot,body)
 SELECT capture_id_value,scope_value,requested::uuid,ordinality-1,
 jsonb_build_object('steps',COALESCE((SELECT jsonb_agg(jsonb_build_object(
 'step_id',s.step_id,'run_id',s.run_id,'activity_id',s.activity_id,'invocation_id',s.invocation_id,'attempt_id',s.attempt_id,
 'logical_step_id',s.logical_step_id,'parent_step_id',s.parent_step_id,'semantic_key',s.semantic_key,'relationship',s.relationship,
 'kind',s.kind,'status',s.status,'started_at',s.started_at,'ended_at',s.ended_at,'duration_ms',s.duration_ms,
 'first_persisted_output_at',s.first_persisted_output_at,'wait_reason',s.wait_reason,'end_reason',s.end_reason,'business_outcome',s.business_outcome,
 'tool_name',s.tool_name,'tool_contract_revision',s.tool_contract_revision,'public_summary',s.public_summary,
 'input_ref',s.input_ref,'output_ref',s.output_ref,'artifact_refs',s.artifact_refs,'citation_refs',s.citation_refs,
 'configuration',s.configuration,'projection_revision',s.projection_revision,'completeness',s.completeness,'schema_version',s.schema_version)
 ORDER BY s.observed_order,s.step_id) FROM public.execution_view_steps s WHERE s.scope_key=scope_value AND s.run_id=requested::uuid),'[]'::jsonb))
 FROM jsonb_array_elements_text(p->'detail_run_ids') WITH ORDINALITY requested(requested,ordinality);

 INSERT INTO public.comparison_artifacts(capture_id,scope_key,run_id,step_id,artifact_id,version,provenance_id,content_digest,storage_key)
 SELECT DISTINCT ON(d.run_id,step->>'step_id',ref->>'artifact_id',ref->>'version') capture_id_value,scope_value,d.run_id,step->>'step_id',ref->>'artifact_id',(ref->>'version')::integer,provenance.id,provenance.content_digest,artifact.version_refs->>((ref->>'version')::integer-1)
 FROM public.comparison_details d JOIN public.comparison_members m ON m.capture_id=d.capture_id AND m.run_id=d.run_id
 CROSS JOIN LATERAL jsonb_array_elements(d.body->'steps') step CROSS JOIN LATERAL jsonb_array_elements(COALESCE(NULLIF(step->'artifact_refs','null'::jsonb),'[]'::jsonb)) ref
 JOIN public.artifact_version_provenance provenance ON provenance.scope_key=scope_value AND provenance.artifact_id=ref->>'artifact_id' AND provenance.version=(ref->>'version')::integer
 AND provenance.producer_run_id=d.run_id AND provenance.producer_step_ids ? (step->>'step_id') AND provenance.binding_status='bound' AND provenance.availability='available' AND provenance.boundary<=m.formal_position AND provenance.content_digest IS NOT NULL
 JOIN public.artifacts artifact ON artifact.id=provenance.artifact_id AND artifact.version_refs->>((ref->>'version')::integer-1) IS NOT NULL
 WHERE d.capture_id=capture_id_value AND ref->>'availability'='available' ORDER BY d.run_id,step->>'step_id',ref->>'artifact_id',ref->>'version',provenance.id;
 UPDATE public.comparison_resources r SET resources=r.resources||COALESCE((SELECT jsonb_agg(DISTINCT jsonb_build_object('resource_kind','artifact','resource_id',a.artifact_id,'resource_version',a.version::text)) FROM public.comparison_artifacts a WHERE a.capture_id=r.capture_id AND a.run_id=r.run_id),'[]'::jsonb) WHERE r.capture_id=capture_id_value;
 INSERT INTO public.comparison_allocations(capture_id,scope_key,id,body) SELECT capture_id_value,scope_value,(a->>'id')::uuid,a FROM jsonb_array_elements(public.opencitadel_comparison_allocations(encoded,signature,capture_id_value)) a;
 UPDATE public.comparison_details d SET body=d.body||jsonb_build_object('semantic_evidence',COALESCE((SELECT jsonb_agg(jsonb_build_object('step_id',step->>'step_id','attempt_id',step->>'attempt_id','case_revision',result.case_revision_id,'semantic_key','recording-slot:'||ledger.version_id::text||':'||ledger.slot_id::text,'tool_contract_revision',slot.body->>'contract_digest','semantic_source','fixed_case','provenance_scope','activity'))
 FROM jsonb_array_elements(d.body->'steps') step JOIN public.evaluation_replay_ledger ledger ON ledger.scope_key=scope_value AND ledger.run_id=d.run_id AND ledger.activity_id::text=step->>'activity_id'
 JOIN public.evaluation_recording_slots slot ON slot.scope_key=ledger.scope_key AND slot.version_id=ledger.version_id AND slot.id=ledger.slot_id AND slot.match_key=ledger.match_key AND slot.body->>'tool'=step->>'tool_name' AND NULLIF(slot.body->>'contract_digest','') IS NOT NULL AND slot.body->>'match_key'=ledger.match_key AND (step->>'tool_contract_revision' IS NULL OR slot.body->>'contract_digest'=step->>'tool_contract_revision')
 JOIN public.evaluation_replay_bindings binding ON binding.scope_key=ledger.scope_key AND binding.run_id=ledger.run_id AND binding.version_id=ledger.version_id
 JOIN public.evaluation_batch_attempts attempt ON attempt.scope_key=scope_value AND attempt.run_id=d.run_id JOIN public.evaluation_batch_results result ON result.scope_key=attempt.scope_key AND result.id=attempt.result_id
 WHERE step->>'status' IN ('completed','failed') AND step->>'attempt_id' IS NOT NULL),'[]'::jsonb)) WHERE d.capture_id=capture_id_value;
 IF p->>'baseline_configuration' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.comparison_scores q WHERE q.capture_id=capture_id_value AND q.body->>'config_id'=p->>'baseline_configuration') THEN RAISE EXCEPTION 'comparison_baseline_unavailable'; END IF;
 receipt_value:=jsonb_build_object('comparison_id',comparison_id_value,'revision',next_revision,'revision_id',capture_id_value,
 'resources',COALESCE((SELECT jsonb_agg(DISTINCT resource) FROM public.comparison_resources r JOIN public.comparison_details d ON d.capture_id=r.capture_id AND d.run_id=r.run_id CROSS JOIN LATERAL jsonb_array_elements(r.resources) resource WHERE r.capture_id=capture_id_value),'[]'::jsonb));
 RETURN public.opencitadel_comparison_receipt(p,command_name,command_target,receipt_value);
END $$
"""

CONTROL = r"""
CREATE FUNCTION public.opencitadel_comparison_control(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;epoch bigint;saved record;answer jsonb;edit jsonb;left_body jsonb;right_body jsonb;next_alignment integer;receipt_value jsonb;normalized_edits jsonb:='[]'::jsonb;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 SELECT c.* INTO saved FROM public.comparison_revisions c WHERE c.scope_key=s AND
 ((p->>'revision_id' IS NOT NULL AND c.id=(p->>'revision_id')::uuid) OR (c.comparison_id=(p->>'comparison_id')::uuid AND c.revision=(p->>'revision')::integer));
 IF NOT FOUND THEN RAISE EXCEPTION 'comparison_not_found'; END IF;
 IF p->>'operation'='owner' THEN RETURN jsonb_build_object('valid',true); END IF;
 IF p->>'operation'='publish' THEN
  IF p->'principal'->>'global_role'='auditor' OR saved.caller_id<>current_setting('app.user_id',true) THEN RAISE EXCEPTION 'comparison_read_only_principal'; END IF;
  IF EXISTS(SELECT 1 FROM public.opencitadel_comparison_current_rows(s,saved.id) c WHERE NOT c.available) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
  IF EXISTS(SELECT 1 FROM public.comparison_resources r JOIN public.comparison_details d ON d.capture_id=r.capture_id AND d.run_id=r.run_id CROSS JOIN LATERAL jsonb_array_elements(r.resources) resource WHERE r.capture_id=saved.id AND NOT EXISTS(
   SELECT 1 FROM public.resource_pins pin WHERE pin.scope_key=s AND pin.owner_kind='comparison_revision' AND pin.owner_id=saved.id::text AND pin.resource_kind=resource->>'resource_kind' AND pin.resource_id=resource->>'resource_id' AND pin.resource_version=resource->>'resource_version' AND pin.available)) THEN RAISE EXCEPTION 'comparison_retention_unavailable'; END IF;
  UPDATE public.comparison_revisions SET published=true WHERE id=saved.id;
  RETURN jsonb_build_object('published',true);
 END IF;
 IF NOT saved.published THEN RAISE EXCEPTION 'comparison_not_found'; END IF;

 IF p->>'operation'='artifact' THEN
  SELECT jsonb_build_object('artifact_id',a.artifact_id,'version',a.version,'content_digest',a.content_digest,'kind',artifact.kind,'storage_key',a.storage_key) INTO answer
  FROM public.comparison_artifacts a JOIN public.opencitadel_comparison_current_rows(s,saved.id,ARRAY[(p->'selection'->>'run_id')::uuid]) current ON current.run_id=a.run_id AND current.available
  JOIN public.artifacts artifact ON artifact.id=a.artifact_id
  JOIN public.artifact_version_provenance provenance ON provenance.id=a.provenance_id AND provenance.scope_key=s AND provenance.availability='available' AND provenance.binding_status='bound' AND provenance.content_digest=a.content_digest
  WHERE a.capture_id=saved.id AND a.run_id=(p->'selection'->>'run_id')::uuid AND a.step_id=p->'selection'->>'step_id' AND a.artifact_id=p->'selection'->>'artifact_id' AND a.version=(p->'selection'->>'version')::integer;
  IF answer IS NULL OR answer->>'storage_key' IS NULL THEN RAISE EXCEPTION 'comparison_artifact_unavailable'; END IF;
  RETURN answer;
 END IF;
 IF p->>'operation'='current' THEN
  SELECT jsonb_build_object('authority_revision',epoch,'manifest',md5(saved.alignment_revision::text||COALESCE(string_agg(c.run_id::text||':'||c.available::text,',' ORDER BY c.run_id),''))) INTO answer
  FROM public.opencitadel_comparison_current_rows(s,saved.id) c;
  RETURN answer;
 END IF;
 IF p->>'operation'='align' THEN
  IF p->'principal'->>'global_role'='auditor' THEN RAISE EXCEPTION 'comparison_read_only_principal'; END IF;
  IF jsonb_typeof(p->'edits') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'edits') NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_comparison_alignment'; END IF;
  receipt_value:=public.opencitadel_comparison_receipt(p,'align',jsonb_build_object('comparison_id',saved.comparison_id,'revision',saved.revision));
  IF receipt_value IS NOT NULL THEN
   IF EXISTS(SELECT endpoint::uuid FROM jsonb_array_elements(p->'edits') e CROSS JOIN LATERAL(VALUES(e->>'left_run_id'),(e->>'right_run_id')) endpoints(endpoint)
    EXCEPT SELECT c.run_id FROM public.opencitadel_comparison_current_rows(s,saved.id,ARRAY(SELECT endpoint::uuid FROM jsonb_array_elements(p->'edits') e CROSS JOIN LATERAL(VALUES(e->>'left_run_id'),(e->>'right_run_id')) endpoints(endpoint))) c WHERE c.available) THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
   RETURN receipt_value;
  END IF;
  UPDATE public.comparison_revisions c SET alignment_revision=c.alignment_revision+1 WHERE c.id=saved.id AND c.alignment_revision=(p->>'expected_revision')::integer RETURNING alignment_revision INTO next_alignment;
  IF NOT FOUND THEN RAISE EXCEPTION 'alignment_conflict'; END IF;
  FOR edit IN SELECT value FROM jsonb_array_elements(p->'edits') LOOP
   IF edit->>'action' NOT IN ('confirm','unpair') THEN RAISE EXCEPTION 'invalid_comparison_alignment'; END IF;
   SELECT d.body INTO left_body FROM public.comparison_details d JOIN public.opencitadel_comparison_current_rows(s,saved.id,ARRAY[(edit->>'left_run_id')::uuid,(edit->>'right_run_id')::uuid]) c ON c.run_id=d.run_id AND c.available WHERE d.capture_id=saved.id AND d.run_id=(edit->>'left_run_id')::uuid;
   SELECT d.body INTO right_body FROM public.comparison_details d JOIN public.opencitadel_comparison_current_rows(s,saved.id,ARRAY[(edit->>'left_run_id')::uuid,(edit->>'right_run_id')::uuid]) c ON c.run_id=d.run_id AND c.available WHERE d.capture_id=saved.id AND d.run_id=(edit->>'right_run_id')::uuid;
   IF left_body IS NULL OR right_body IS NULL OR (SELECT count(*) FROM jsonb_array_elements(left_body->'steps') step WHERE step->>'step_id'=edit->>'left_step_id' AND (NOT(edit ? 'left_attempt_id') OR step->>'attempt_id' IS NOT DISTINCT FROM edit->>'left_attempt_id'))<>1 OR (SELECT count(*) FROM jsonb_array_elements(right_body->'steps') step WHERE step->>'step_id'=edit->>'right_step_id' AND (NOT(edit ? 'right_attempt_id') OR step->>'attempt_id' IS NOT DISTINCT FROM edit->>'right_attempt_id'))<>1 THEN RAISE EXCEPTION 'comparison_member_unavailable'; END IF;
   edit:=edit||jsonb_build_object('left_attempt_id',(SELECT step->>'attempt_id' FROM jsonb_array_elements(left_body->'steps') step WHERE step->>'step_id'=edit->>'left_step_id' AND (NOT(edit ? 'left_attempt_id') OR step->>'attempt_id' IS NOT DISTINCT FROM edit->>'left_attempt_id')),'right_attempt_id',(SELECT step->>'attempt_id' FROM jsonb_array_elements(right_body->'steps') step WHERE step->>'step_id'=edit->>'right_step_id' AND (NOT(edit ? 'right_attempt_id') OR step->>'attempt_id' IS NOT DISTINCT FROM edit->>'right_attempt_id')));
   DELETE FROM public.comparison_alignment_pairs a WHERE a.capture_id=saved.id AND a.pair_key=least(edit->>'left_run_id',edit->>'right_run_id')||':'||greatest(edit->>'left_run_id',edit->>'right_run_id')
    AND ((a.left_run_id::text=edit->>'left_run_id' AND a.left_step_id=edit->>'left_step_id' AND a.left_attempt_id=COALESCE(edit->>'left_attempt_id','')) OR (a.right_run_id::text=edit->>'left_run_id' AND a.right_step_id=edit->>'left_step_id' AND a.right_attempt_id=COALESCE(edit->>'left_attempt_id','')) OR (a.left_run_id::text=edit->>'right_run_id' AND a.left_step_id=edit->>'right_step_id' AND a.left_attempt_id=COALESCE(edit->>'right_attempt_id','')) OR (a.right_run_id::text=edit->>'right_run_id' AND a.right_step_id=edit->>'right_step_id' AND a.right_attempt_id=COALESCE(edit->>'right_attempt_id','')));
   INSERT INTO public.comparison_alignment_pairs(capture_id,scope_key,pair_key,left_run_id,left_step_id,left_attempt_id,right_run_id,right_step_id,right_attempt_id,revision,supersedes,author,body)
   VALUES(saved.id,s,least(edit->>'left_run_id',edit->>'right_run_id')||':'||greatest(edit->>'left_run_id',edit->>'right_run_id'),(edit->>'left_run_id')::uuid,edit->>'left_step_id',COALESCE(edit->>'left_attempt_id',''),(edit->>'right_run_id')::uuid,edit->>'right_step_id',COALESCE(edit->>'right_attempt_id',''),next_alignment,(p->>'expected_revision')::integer,current_setting('app.user_id',true),edit);
   normalized_edits:=normalized_edits||jsonb_build_array(edit);
  END LOOP;
  INSERT INTO public.comparison_alignments(capture_id,scope_key,revision,supersedes,author,body) VALUES(saved.id,s,next_alignment,(p->>'expected_revision')::integer,current_setting('app.user_id',true),normalized_edits);
  RETURN public.opencitadel_comparison_receipt(p,'align',jsonb_build_object('comparison_id',saved.comparison_id,'revision',saved.revision),jsonb_build_object('alignment_revision',next_alignment));
 END IF;
 RAISE EXCEPTION 'invalid_comparison_request';
END $$
"""

RUN_GROUPS = r"""WITH inputs AS(SELECT f.* FROM public.comparison_members m CROSS JOIN LATERAL jsonb_to_record(m.run_fact) AS f(family text,purpose text,execution_mode text,configuration_revision text,status text,admitted_at timestamptz,terminal_at timestamptz) WHERE m.capture_id=saved.id AND m.run_id=ANY(visible))
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
ORDER BY family,purpose,execution_mode,configuration_revision,bucket"""
READ_PREFIX = r"""
CREATE FUNCTION public.opencitadel_comparison_read(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;epoch bigint;saved record;visible uuid[];accounting uuid[];section jsonb;facts jsonb;result jsonb;
 page_limit integer;after_ordinal integer;grain_value text;timezone_value text;accounting_mode text;selected_batch text;stamp text;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';
 IF p->>'operation' IS DISTINCT FROM 'read' THEN RAISE EXCEPTION 'invalid_comparison_request'; END IF;
 SELECT c.* INTO saved FROM public.comparison_revisions c WHERE c.scope_key=s AND c.comparison_id=(p->>'comparison_id')::uuid AND c.revision=(p->>'revision')::integer AND c.published;
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
"""
READ_BETWEEN = r"""
 ) x;
 facts:=facts||jsonb_build_object('run_groups',section);
"""
READ_SUFFIX = r"""
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


def interval_groups(column, fields):
    """Reaggregate retained per-Run sufficient statistics with the A01 group identity."""
    selections = ",".join(f"sum((m.{column}->>'{name}')::numeric) AS {name}" for name in fields)
    return (
        r"""SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
 SELECT m.run_fact->>'family' AS family,m.run_fact->>'purpose' AS purpose,m.run_fact->>'execution_mode' AS execution_mode,m.run_fact->>'configuration_revision' AS configuration_revision,
 CASE WHEN grain_value='hour' THEN date_trunc('hour',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE 'UTC' - (((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value)-((m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE 'UTC')) ELSE date_trunc('day',(m.run_fact->>'admitted_at')::timestamptz AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,
 """
        + selections
        + f""" FROM public.comparison_members m WHERE m.capture_id=saved.id AND m.run_id=ANY(visible) AND m.{column} IS NOT NULL GROUP BY family,purpose,execution_mode,configuration_revision,bucket
 ) x;
 facts:=facts||jsonb_build_object('{"intervals" if column == "interval_fact" else "approvals"}',section);
"""
    )


IMMUTABILITY = r"""
CREATE FUNCTION public.opencitadel_comparison_immutable() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE revision_id uuid;
BEGIN
 IF TG_TABLE_NAME='comparison_revisions' THEN
  IF OLD.published AND (to_jsonb(OLD)-'alignment_revision') IS DISTINCT FROM (to_jsonb(NEW)-'alignment_revision') THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
 revision_id:=NEW.capture_id;
 IF EXISTS(SELECT 1 FROM public.comparison_revisions c WHERE c.id=revision_id AND c.published) THEN RAISE EXCEPTION 'comparison_immutable'; END IF;
 RETURN NEW;
END $$
"""


DIFF_AUTH = r"""
CREATE FUNCTION public.opencitadel_comparison_diff_authorized(s text,capture uuid,selection jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer boolean;
BEGIN
 IF selection->>'format' NOT IN ('text','json') THEN RETURN false; END IF;
 WITH current_rows AS MATERIALIZED(SELECT * FROM public.opencitadel_comparison_current_rows(s,capture,ARRAY[(selection->'left'->>'run_id')::uuid,(selection->'right'->>'run_id')::uuid]))
 SELECT bool_and(EXISTS(SELECT 1 FROM public.comparison_artifacts a
  JOIN public.comparison_revisions revision ON revision.id=a.capture_id AND revision.scope_key=s AND revision.published
  JOIN current_rows c ON c.run_id=a.run_id AND c.available
  JOIN public.artifact_version_provenance provenance ON provenance.id=a.provenance_id AND provenance.scope_key=s AND provenance.binding_status='bound' AND provenance.availability='available' AND provenance.content_digest=a.content_digest
  WHERE a.capture_id=capture AND a.scope_key=s AND a.run_id=(sides.side->>'run_id')::uuid AND a.step_id=sides.side->>'step_id' AND a.artifact_id=sides.side->>'artifact_id' AND a.version=(sides.side->>'version')::integer)) INTO answer
 FROM (VALUES(selection->'left'),(selection->'right')) sides(side);
 RETURN COALESCE(answer,false);
END $$
"""
DIFF_JOBS = r"""
CREATE FUNCTION public.opencitadel_comparison_jobs(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb;s text;actor text;capture uuid;job record;job_id uuid;page integer;body text;total_bytes integer;receipt_value jsonb;receipt_target jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);p:=encoded::jsonb;s:=p->>'scope';actor:=current_setting('app.user_id',true);
 IF p->>'operation'='enqueue' THEN
  IF p->'principal'->>'global_role'='auditor' THEN RAISE EXCEPTION 'comparison_read_only_principal'; END IF;
  SELECT c.id INTO capture FROM public.comparison_revisions c WHERE c.scope_key=s AND c.comparison_id=(p->>'comparison_id')::uuid AND c.revision=(p->>'revision')::integer AND c.published;
  IF capture IS NULL OR NOT public.opencitadel_comparison_diff_authorized(s,capture,p->'selection') THEN RAISE EXCEPTION 'comparison_artifact_unavailable'; END IF;
  receipt_target:=jsonb_build_object('comparison_id',p->'comparison_id','revision',p->'revision');
  receipt_value:=public.opencitadel_comparison_receipt(p,'artifact_diff',receipt_target);
  IF receipt_value IS NOT NULL THEN RETURN receipt_value; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('comparison-diff:'||s||':'||actor,0));
  SELECT j.id INTO job_id FROM public.comparison_diff_jobs j WHERE j.scope_key=s AND j.capture_id=capture AND j.caller_id=actor AND j.selection=p->'selection' AND j.status IN ('queued','running','complete','partial') ORDER BY j.created_at DESC LIMIT 1;
  IF job_id IS NOT NULL THEN RETURN public.opencitadel_comparison_receipt(p,'artifact_diff',receipt_target,jsonb_build_object('job_id',job_id)); END IF;
  IF (SELECT count(*) FROM public.comparison_diff_jobs j WHERE j.scope_key=s AND j.caller_id=actor AND j.status IN ('queued','running'))>=20 THEN RAISE EXCEPTION 'comparison_diff_capacity'; END IF;
  job_id:=public.gen_random_uuid();
  INSERT INTO public.comparison_diff_jobs(id,scope_key,capture_id,caller_id,principal,owner_scope,selection,status)
  VALUES(job_id,s,capture,actor,p->'principal',jsonb_build_object('type',CASE WHEN s LIKE 'team:%' THEN 'team' ELSE 'personal' END,'user_id',actor,'team_id',CASE WHEN s LIKE 'team:%' THEN substring(s FROM 6) END),p->'selection','queued');
  RETURN public.opencitadel_comparison_receipt(p,'artifact_diff',receipt_target,jsonb_build_object('job_id',job_id));
 END IF;
 SELECT j.* INTO job FROM public.comparison_diff_jobs j WHERE j.id=(p->>'job_id')::uuid AND j.scope_key=s AND j.caller_id=actor FOR UPDATE;
 IF NOT FOUND OR NOT public.opencitadel_comparison_diff_authorized(s,job.capture_id,job.selection) THEN RAISE EXCEPTION 'comparison_artifact_unavailable'; END IF;
 IF p->>'operation'='publish' THEN
  IF job.status<>'running' OR job.lease_token IS DISTINCT FROM (p->>'lease_token')::uuid OR job.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'comparison_diff_lease_lost'; END IF;
  IF jsonb_typeof(p->'pages') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'pages')>16 OR jsonb_typeof(p->'head') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'comparison_diff_output_limit'; END IF;
  SELECT COALESCE(sum(octet_length(value)),0) INTO total_bytes FROM jsonb_array_elements_text(p->'pages');
  IF total_bytes>1048576 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p->'pages') AS page_values(value) WHERE octet_length(page_values.value)>65536) OR octet_length((p->'head')::text)>8192 THEN RAISE EXCEPTION 'comparison_diff_output_limit'; END IF;
  UPDATE public.comparison_diff_jobs SET status=CASE WHEN (p->'head'->>'complete')::boolean THEN 'complete' ELSE 'partial' END,body=p->'head',lease_token=NULL,lease_until=NULL WHERE id=job.id;
  INSERT INTO public.comparison_diff_pages(job_id,scope_key,ordinal,body) SELECT job.id,s,ordinality-1,value FROM jsonb_array_elements_text(p->'pages') WITH ORDINALITY;
  RETURN jsonb_build_object('published',true);
 END IF;
 IF p->>'operation'='authorize' THEN RETURN jsonb_build_object('authorized',true); END IF;
 IF p->>'operation'='page' THEN
  page:=COALESCE((p->>'page')::integer,0);
  IF page NOT BETWEEN 0 AND 15 THEN RAISE EXCEPTION 'invalid_comparison_cursor'; END IF;
  SELECT d.body INTO body FROM public.comparison_diff_pages d WHERE d.job_id=job.id AND d.ordinal=page;
  RETURN jsonb_build_object('job_id',job.id,'status',job.status,'result',job.body,'encoding','json-utf8','content',body,
   'next_page',CASE WHEN EXISTS(SELECT 1 FROM public.comparison_diff_pages d WHERE d.job_id=job.id AND d.ordinal=page+1) THEN page+1 END);
 END IF;
 RAISE EXCEPTION 'invalid_comparison_request';
END $$
"""
DIFF_CLAIM = r"""
CREATE FUNCTION public.opencitadel_comparison_diff_claim(failed_id uuid DEFAULT NULL,failed_lease uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE job record;answer jsonb;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true)<>'system' OR current_setting('app.system_actor',true)<>'execution-kernel' THEN RAISE EXCEPTION 'comparison_authorization_invalid'; END IF;
 IF failed_id IS NOT NULL THEN
  UPDATE public.comparison_diff_jobs j SET status='failed',lease_token=NULL,lease_until=NULL,body=NULL WHERE j.id=failed_id AND j.lease_token=failed_lease AND j.status='running';
  RETURN NULL;
 END IF;
 SELECT j.* INTO job FROM public.comparison_diff_jobs j WHERE j.status='queued' OR (j.status='running' AND j.lease_until<clock_timestamp()) ORDER BY j.created_at,j.id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 UPDATE public.comparison_diff_jobs j SET status='running',lease_token=public.gen_random_uuid(),lease_until=clock_timestamp()+interval '60 seconds' WHERE j.id=job.id RETURNING j.* INTO job;
 SELECT jsonb_build_object('id',job.id,'lease_token',job.lease_token,'principal',job.principal,'scope',job.owner_scope,'comparison_id',r.comparison_id,'revision',r.revision,'selection',job.selection) INTO answer FROM public.comparison_revisions r WHERE r.id=job.capture_id;
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
        if name in {"comparison_diff_jobs", "comparison_diff_pages", "comparison_revisions"}:
            bind.execute(
                sa.text(
                    f"CREATE POLICY comparison_kernel_jobs ON public.{name} TO {quote(owner)} USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')"
                )
            )
        scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
        valid = "(SELECT public.opencitadel_authorization_valid()) AND current_setting('app.auth_mode',true)='user'"
        bind.execute(
            sa.text(
                f"CREATE POLICY comparison_scope ON public.{name} TO {quote(owner)} USING ({valid} AND {scoped}) WITH CHECK ({valid} AND {scoped})"
            )
        )
    for table in (
        "comparison_scores",
        "comparison_usage",
        "comparison_resources",
        "comparison_accounting_links",
    ):
        bind.execute(sa.text(f"CREATE INDEX ON public.{table}(capture_id,run_id)"))
    bind.execute(sa.text(IMMUTABILITY))
    for table in (
        "comparison_revisions",
        "comparison_members",
        "comparison_accounting",
        "comparison_usage",
        "comparison_scores",
        "comparison_resources",
        "comparison_accounting_links",
        "comparison_details",
        "comparison_artifacts",
        "comparison_allocations",
        "comparison_alignments",
    ):
        bind.execute(
            sa.text(
                f"CREATE TRIGGER comparison_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_comparison_immutable()"
            )
        )
    functions = [
        ACCOUNTING,
        PRIVATE_FACTS,
        SCORE_FACTS,
        ALLOCATIONS,
        RECEIPT,
        RESOURCE_CURRENT,
        CURRENT,
        DIFF_AUTH,
        DIFF_JOBS,
        DIFF_CLAIM,
        CREATE_PREFIX
        + SELECTION
        + CREATE_AFTER_SELECTION
        + INTERVAL_FACTS
        + CREATE_BETWEEN_FACTS
        + APPROVAL_FACTS
        + CREATE_BEFORE_BINDINGS
        + RESOURCE_BINDINGS
        + CREATE_SUFFIX,
        CONTROL,
        READ_PREFIX
        + RUN_GROUPS
        + READ_BETWEEN
        + interval_groups(
            "interval_fact",
            (
                "activity_occupancy_ms",
                "activity_samples",
                "activity_missing",
                "tool_work_ms",
                "tool_samples",
                "tool_missing",
                "tool_terminal",
                "tool_errors",
                "tool_excluded",
                "tool_execution_errors",
                "tool_business_errors",
                "tool_unknown",
                "tool_deferred",
                "tool_cancelled",
            ),
        )
        + interval_groups("approval_fact", ("approval_wait_ms", "samples", "missing"))
        + READ_SUFFIX,
    ]
    for sql in functions:
        bind.execute(sa.text(sql))
    for name, signature in (
        ("accounting", "text,text,uuid"),
        ("usage", "text,text,uuid"),
        ("scores", "text,text,uuid"),
        ("allocations", "text,text,uuid"),
        ("receipt", "jsonb,text,jsonb,jsonb"),
        ("resource_rows", "text,uuid,uuid[]"),
        ("current_rows", "text,uuid,uuid[]"),
        ("materialize", "text,text"),
        ("control", "text,text"),
        ("read", "text,text"),
        ("jobs", "text,text"),
        ("diff_authorized", "text,uuid,jsonb"),
        ("diff_claim", "uuid,uuid"),
    ):
        function = f"public.opencitadel_comparison_{name}({signature})"
        bind.execute(
            sa.text(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        if name in {"materialize", "control", "read", "jobs"}:
            bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(api)}"))
        if name in {"control", "read", "jobs", "diff_claim"}:
            bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(kernel)}"))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_comparison_immutable() FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )


def downgrade():
    raise RuntimeError("Durable retained comparisons require an explicit forward migration")
