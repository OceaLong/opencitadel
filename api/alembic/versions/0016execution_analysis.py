"""Transactional authority stamp and narrowly authorized metric reads."""

import sqlalchemy as sa

from alembic import op

revision = "0016execution_analysis"
down_revision = "0015evaluation_archives"
branch_labels = None
depends_on = None

AUTHORITY = r"""
CREATE FUNCTION public.opencitadel_analysis_authority(encoded text, signature text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; principal jsonb; actor text; team text; s text; secret text; mac bytea;
supplied bytea; difference integer:=0; i integer; answer bigint;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
 OR encoded IS NULL OR octet_length(encoded)>67108864 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
 mac:=public.hmac(convert_to('opencitadel:analysis:authority:v1:'||encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256'); supplied:=decode(signature,'hex');
 FOR i IN 0..31 LOOP difference:=difference | (get_byte(mac,i) # get_byte(supplied,i)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 p:=encoded::jsonb; principal:=p->'principal'; actor:=principal->>'user_id'; team:=NULLIF(current_setting('app.team_id',true),'');
 s:=CASE WHEN team IS NULL THEN 'user:'||actor ELSE 'team:'||team END;
 IF actor IS DISTINCT FROM current_setting('app.user_id',true) OR p->>'scope' IS DISTINCT FROM s
 OR p->>'authorization_signature' IS DISTINCT FROM current_setting('app.auth_signature',true)
 OR (p->>'expires')::numeric < extract(epoch FROM clock_timestamp())
 THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 -- This single MVCC statement binds the epoch to current identity/membership.
 SELECT e.revision INTO answer FROM public.analysis_authority_epoch e
 JOIN public.users u ON u.id=actor AND u.status='active'
 AND u.token_version=(principal->>'token_version')::integer AND u.global_role=principal->>'global_role'
 WHERE e.singleton AND (team IS NULL OR EXISTS(SELECT 1 FROM public.team_members m
 WHERE m.team_id=team AND m.user_id=actor AND m.role=principal->'team_roles'->>team));
 IF answer IS NULL THEN RAISE EXCEPTION 'analysis_authorization_revoked'; END IF;
 RETURN answer;
END $$
"""


PRIVATE_FACTS = r"""
CREATE FUNCTION public.opencitadel_analysis_usage(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; result jsonb; rev bigint;
BEGIN
 rev:=public.opencitadel_analysis_authority(encoded,signature);
 actor:=current_setting('app.user_id',true);
 SELECT scope_key INTO s FROM public.analysis_captures WHERE id=capture AND caller_id=actor AND expires_at>clock_timestamp();
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 WITH selected_runs AS (SELECT run_id,formal_position FROM public.analysis_capture_accounting WHERE capture_id=capture AND scope_key=s),
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
CREATE FUNCTION public.opencitadel_analysis_scores(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; result jsonb; rev bigint;
BEGIN
 rev:=public.opencitadel_analysis_authority(encoded,signature);
 actor:=current_setting('app.user_id',true);
 SELECT scope_key INTO s FROM public.analysis_captures WHERE id=capture AND caller_id=actor AND expires_at>clock_timestamp();
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 WITH members AS (SELECT DISTINCT r.id AS result_id,r.case_revision_id AS case_id,r.config_version_id AS config_id,r.execution_status,r.batch_id,a.run_id,
 v.family,t.dataset_version,t.body->>'mode' AS mode,COALESCE(t.body->>'environment_version','recorded') AS environment_version,t.rubric_version,
 COALESCE(h.revision,0) AS evaluation_revision
 FROM public.analysis_capture_members m JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.run_id=m.run_id
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
 SELECT COALESCE(jsonb_agg(jsonb_build_object('result_id',m.result_id,'case_id',m.case_id,'config_id',m.config_id,'execution_status',m.execution_status,
 'batch_id',m.batch_id,'family',m.family,'dataset_version',m.dataset_version,'mode',m.mode,'environment_version',m.environment_version,'rubric',r.rubric,'evaluation_revision',m.evaluation_revision,
 'required_conditions',COALESCE(v.body->'required_conditions','[]'::jsonb),
 'source_sets',COALESCE((SELECT jsonb_agg(jsonb_build_object('source',t.source,'required_dimensions',t.required_dimensions,'applicable_dimensions',t.applicable_dimensions,'source_set_id',t.id)) FROM sets t WHERE t.result_id=m.result_id AND t.rubric_revision=r.rubric),'[]'::jsonb),
 'scores',COALESCE((SELECT jsonb_agg(jsonb_build_object('source',h.source,'dimension',h.dimension,'value',h.value,'status',h.status,'invalidated',h.invalidated,'source_set_id',h.source_set_id)) FROM heads h WHERE h.result_id=m.result_id AND h.rubric_revision=r.rubric),'[]'::jsonb))),'[]'::jsonb)
 INTO result FROM members m JOIN rubrics r ON r.result_id=m.result_id JOIN public.evaluation_rubric_versions v ON v.scope_key=s AND v.id=r.rubric;
 RETURN result;
END $$
"""

ACCOUNTING = r"""
CREATE FUNCTION public.opencitadel_analysis_accounting(encoded text, signature text, capture uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; actor text; mode text; batch text; n integer; saved jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 actor:=current_setting('app.user_id',true);
 SELECT scope_key,query INTO s,saved FROM public.analysis_captures WHERE id=capture AND caller_id=actor AND expires_at>clock_timestamp();
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 SELECT item->>1 INTO mode FROM jsonb_array_elements(saved->'filters') item WHERE item->>0='accounting';
 SELECT item->>1 INTO batch FROM jsonb_array_elements(saved->'filters') item WHERE item->>0='batch_id';
 mode:=COALESCE(mode,'run');
 IF mode NOT IN ('run','selected_result','batch_total') OR (mode='batch_total' AND batch IS NULL) THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 WITH selected_results AS(SELECT DISTINCT r.id,r.batch_id FROM public.evaluation_batch_results r WHERE r.scope_key=s AND
 ((mode='batch_total' AND r.batch_id::text=batch) OR (mode='selected_result' AND EXISTS(SELECT 1 FROM public.evaluation_batch_attempts a JOIN public.analysis_capture_members m ON m.scope_key=s AND m.run_id=a.run_id AND m.capture_id=capture WHERE a.scope_key=s AND a.result_id=r.id)))),
 linked AS(SELECT run_id FROM public.analysis_capture_members WHERE scope_key=s AND capture_id=capture AND mode='run'
 UNION SELECT a.run_id FROM selected_results r JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.result_id=r.id
 UNION SELECT j.run_id FROM selected_results r JOIN public.evaluation_judge_intents j ON j.scope_key=s AND j.result_id=r.id),
 bounded AS(SELECT run_id FROM linked ORDER BY run_id LIMIT 1000001),
 inserted AS(INSERT INTO public.analysis_capture_accounting(capture_id,scope_key,run_id,formal_position,progress_position,observed_order,projector_version,generation,ordinal)
 SELECT capture,s,b.run_id,COALESCE(r.formal_position,0),COALESCE(r.progress_position,0),COALESCE(r.observed_order,0),COALESCE(r.projector_version,1),
 COALESCE((SELECT active_generation::text FROM public.execution_view_controls WHERE scope_key=s),'live'),row_number() OVER(ORDER BY b.run_id)-1
 FROM bounded b LEFT JOIN public.execution_view_runs r ON r.scope_key=s AND r.run_id=b.run_id RETURNING run_id)
 SELECT count(*) INTO n FROM inserted;
 IF n>1000000 THEN RAISE EXCEPTION 'analysis_accounting_capacity_exceeded'; END IF;
 RETURN n;
END $$
"""

JUDGE_RESOURCES = r"""
CREATE FUNCTION public.opencitadel_analysis_judge_resources(encoded text, signature text, members jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; result jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 s:=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END;
 IF jsonb_typeof(members)<>'array' OR jsonb_array_length(members)>1100000 THEN RAISE EXCEPTION 'analysis_capacity_exceeded'; END IF;
 WITH ids AS(SELECT (m->>'run_id')::uuid AS run_id FROM jsonb_array_elements(members) m),
 refs AS(SELECT DISTINCT m.run_id,r->>'resource_kind' AS kind,r->>'resource_id' AS id,r->>'resource_version' AS version
 FROM ids m JOIN public.evaluation_judge_intents j ON j.scope_key=s AND (j.run_id=m.run_id OR j.candidate->>'run_id'=m.run_id::text)
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(j.materials->'resources','[]'::jsonb)) r)
 SELECT COALESCE(jsonb_agg(jsonb_build_object('run_id',run_id,'kind',kind,'id',id,'version',version)),'[]'::jsonb) INTO result FROM refs;
 RETURN result;
END $$
"""

CAPTURE_MANIFEST = r"""
CREATE FUNCTION public.opencitadel_analysis_manifest(encoded text, signature text, members_json jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE scope_value text; owner_value text; team_value text; resources_json jsonb; rows_json jsonb; fingerprint text;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END;
 scope_value:=CASE WHEN team_value IS NULL THEN 'user:'||owner_value ELSE 'team:'||team_value END;
 resources_json:=public.opencitadel_analysis_judge_resources(encoded,signature,members_json);
 SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.run_id),'[]'::jsonb),
 md5(COALESCE(string_agg(x.run_id::text||':'||COALESCE(x.available,false)::text||':'||x.manifest,'|' ORDER BY x.run_id),''))
 INTO rows_json,fingerprint FROM (

WITH members AS (SELECT * FROM jsonb_to_recordset(CAST(members_json AS jsonb)) AS m(run_id uuid,formal_position bigint,progress_position bigint,observed_order bigint,projector_version integer,generation text)),
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
 UNION SELECT r.run_id,p.resource_kind AS kind,p.resource_id AS id,p.resource_version AS version,EXISTS(SELECT 1 FROM public.resource_pins live WHERE live.scope_key=p.scope_key AND live.owner_kind=p.owner_kind AND live.owner_id=p.owner_id AND live.resource_kind=p.resource_kind AND live.resource_id=p.resource_id AND live.resource_version=p.resource_version AND live.available) AS pinned
 FROM runs r JOIN public.analysis_required_pins p ON p.scope_key=scope_value AND (
 (p.owner_kind='run' AND p.owner_id=r.run_id::text) OR
 (p.owner_kind='session' AND r.source->>'entity_type'='session' AND p.owner_id=r.source->>'entity_id') OR
 EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND o.kind=p.owner_kind AND o.id=p.owner_id))
 UNION SELECT m.run_id,'execution_content',c.content_id::text,c.content_digest,true
 FROM members m JOIN public.execution_content_bindings b ON b.scope_key=scope_value AND b.run_id=m.run_id AND b.formal_position<=m.formal_position
 JOIN public.execution_public_content c ON c.scope_key=b.scope_key AND c.content_id=b.content_id),
checks AS (SELECT f.*,f.pinned AND CASE f.kind
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
 ELSE false END AS available FROM refs f),
coverage AS (SELECT m.run_id,m.visible,m.source,m.completeness,count(o.run_id) AS count,
 md5(COALESCE(string_agg(o.observed_order::text,',' ORDER BY o.observed_order),'')) AS digest
 FROM runs m LEFT JOIN public.execution_view_observations o ON o.scope_key=scope_value AND o.run_id=m.run_id AND o.projector_version=m.projector_version
 AND o.observed_order<=m.observed_order AND o.formal_position<=m.formal_position AND o.progress_position<=m.progress_position GROUP BY m.run_id,m.visible,m.source,m.completeness)
SELECT r.run_id,r.visible AND COALESCE(bool_and(c.available),true)
 AND NOT EXISTS(SELECT 1 FROM version_owners o WHERE o.run_id=r.run_id AND (
 (o.kind='dataset_version' AND (NOT EXISTS(SELECT 1 FROM public.analysis_dataset_pin_coverage proof WHERE proof.scope_key=scope_value AND proof.version_id::text=o.id)
 OR NOT EXISTS(SELECT 1 FROM public.evaluation_dataset_versions d WHERE d.scope_key=scope_value AND d.id::text=o.id)
 OR EXISTS(SELECT 1 FROM public.evaluation_version_cases vc JOIN public.evaluation_case_revisions cr ON cr.scope_key=vc.scope_key AND cr.id=vc.case_revision_id LEFT JOIN public.evaluation_object_intents ob ON ob.scope_key=cr.scope_key AND ob.id=cr.object_id WHERE vc.scope_key=scope_value AND vc.version_id::text=o.id AND (ob.id IS NULL OR ob.cleaned_at IS NOT NULL))))
 OR (o.kind='recording_version' AND (NOT EXISTS(SELECT 1 FROM public.evaluation_recording_versions rv WHERE rv.scope_key=scope_value AND rv.id::text=o.id)
 OR EXISTS(SELECT 1 FROM public.evaluation_recording_slots rs LEFT JOIN public.evaluation_recording_objects ro ON ro.scope_key=rs.scope_key AND ro.id=rs.object_id WHERE rs.scope_key=scope_value AND rs.version_id::text=o.id AND (ro.id IS NULL OR ro.cleaned_at IS NOT NULL)))))) AS available,
 md5(jsonb_build_object('generation',COALESCE((SELECT active_generation::text FROM public.execution_view_controls WHERE scope_key=scope_value),'live'),
 'source',r.source,'coverage',r.digest,'count',r.count,'missing',r.completeness->'missing_intervals',
 'resources',COALESCE(jsonb_agg(jsonb_build_array(c.kind,c.id,c.version,c.pinned,c.available) ORDER BY c.kind,c.id,c.version) FILTER(WHERE c.id IS NOT NULL),'[]'::jsonb))::text) AS manifest
FROM coverage r LEFT JOIN checks c ON c.run_id=r.run_id
GROUP BY r.run_id,r.visible,r.source,r.completeness,r.digest,r.count
ORDER BY r.run_id

 ) x;
 RETURN jsonb_build_object('rows',rows_json,'manifest',fingerprint);
END $$
"""
RESOURCE_AVAILABILITY = r"""
CREATE FUNCTION public.opencitadel_analysis_resources_available(encoded text, signature text, resources jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE scope_value text; owner_value text; team_value text; result boolean;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true) ELSE NULL END;
 scope_value:=CASE WHEN team_value IS NULL THEN 'user:'||owner_value ELSE 'team:'||team_value END;
 SELECT COALESCE(bool_and(CASE f.kind
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
 ELSE false END),true) INTO result FROM
 (SELECT r->>'resource_kind' AS kind,r->>'resource_id' AS id,r->>'resource_version' AS version FROM jsonb_array_elements(resources) r) f;
 RETURN result;
END $$
"""

CAPTURE = r"""
CREATE FUNCTION public.opencitadel_analysis_capture(encoded text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p jsonb; actor text; scope_value text; owner_value text; team_value text; epoch bigint; capture_id uuid;
 query_value jsonb; saved record; members_json jsonb; checked jsonb; fingerprint text; n integer;
 start_at timestamptz; end_at timestamptz; grain_value text; timezone_value text; captured_at_value timestamptz;
 q_family text; q_status text; q_purpose text; q_mode text; q_configuration_revision text; q_model_revision text;
 unavailable_coverage boolean; q_session text; q_tool text; q_batch_id text; filters jsonb; result jsonb; section jsonb;
BEGIN
 epoch:=public.opencitadel_analysis_authority(encoded,signature);
 p:=encoded::jsonb; actor:=current_setting('app.user_id',true); team_value:=NULLIF(current_setting('app.team_id',true),'');
 owner_value:=CASE WHEN team_value IS NULL THEN actor ELSE NULL END;
 scope_value:=CASE WHEN team_value IS NULL THEN 'user:'||actor ELSE 'team:'||team_value END;
 IF p->>'operation' IN ('read','current','seal') THEN
  capture_id:=(p->>'capture_id')::uuid;
  SELECT c.* INTO saved FROM public.analysis_captures c WHERE c.id=opencitadel_analysis_capture.capture_id AND c.scope_key=scope_value AND c.caller_id=actor AND c.expires_at>clock_timestamp();
  IF NOT FOUND OR saved.authority_revision<>epoch THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO members_json FROM (
   SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_members WHERE scope_key=scope_value AND capture_id=saved.id
   UNION SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_accounting WHERE scope_key=scope_value AND capture_id=saved.id) x;
  checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') r WHERE (r->>'available')::boolean IS DISTINCT FROM true) THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  fingerprint:=checked->>'manifest';
  IF public.opencitadel_analysis_authority(encoded,signature)<>epoch THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  IF p->>'operation'='seal' THEN
   IF p->>'manifest' IS DISTINCT FROM fingerprint OR jsonb_typeof(p->'metrics') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'analysis_capture_invalid'; END IF;
   INSERT INTO public.analysis_capture_metrics(capture_id,scope_key,manifest,body) VALUES(saved.id,scope_value,fingerprint,p->'metrics');
   RETURN jsonb_build_object('watermark',saved.id,'authority_revision',epoch,'manifest',fingerprint);
  END IF;
  SELECT m.* INTO saved FROM public.analysis_capture_metrics m WHERE m.capture_id=opencitadel_analysis_capture.capture_id AND m.scope_key=scope_value;
  IF NOT FOUND OR saved.manifest IS DISTINCT FROM fingerprint THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
  IF p->>'operation'='current' THEN RETURN jsonb_build_object('authority_revision',epoch,'manifest',fingerprint); END IF;
  RETURN jsonb_build_object('watermark',opencitadel_analysis_capture.capture_id,'authority_revision',epoch,'manifest',fingerprint,'metrics',saved.body,
    'query',(SELECT c.query FROM public.analysis_captures c WHERE c.id=opencitadel_analysis_capture.capture_id AND c.scope_key=scope_value));
 END IF;
 IF p->>'operation' IS DISTINCT FROM 'begin' THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 query_value:=p->'query'; start_at:=(query_value->>'start')::timestamptz; end_at:=(query_value->>'end')::timestamptz;
 grain_value:=query_value->>'grain'; timezone_value:=query_value->>'timezone';
 IF start_at IS NULL OR end_at IS NULL OR end_at<=start_at OR end_at-start_at>interval '90 days' OR grain_value NOT IN ('hour','day')
 OR NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=timezone_value) OR jsonb_typeof(query_value->'filters') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(query_value->'filters') f WHERE jsonb_array_length(f)<>2 OR f->>0 NOT IN ('family','status','purpose','mode','configuration_revision','model_revision','session','tool','batch_id','accounting') OR jsonb_typeof(f->1)<>'string') THEN RAISE EXCEPTION 'analysis_query_invalid'; END IF;
 SELECT COALESCE(jsonb_object_agg(f->>0,f->1),'{}'::jsonb) INTO filters FROM jsonb_array_elements(query_value->'filters') f;
 q_family:=filters->>'family';q_status:=filters->>'status';q_purpose:=filters->>'purpose';q_mode:=filters->>'mode';q_configuration_revision:=filters->>'configuration_revision';q_model_revision:=filters->>'model_revision';q_session:=filters->>'session';q_tool:=filters->>'tool';q_batch_id:=filters->>'batch_id';
 INSERT INTO public.analysis_capture_slots(scope_key,caller_id,revision) VALUES(scope_value,actor,1)
 ON CONFLICT(scope_key,caller_id) DO UPDATE SET revision=public.analysis_capture_slots.revision+1;
 SELECT c.id,m.body,m.manifest INTO saved FROM public.analysis_captures c JOIN public.analysis_capture_metrics m ON m.scope_key=c.scope_key AND m.capture_id=c.id
 WHERE c.scope_key=scope_value AND c.caller_id=actor AND c.query=query_value AND c.authority_revision=epoch AND c.expires_at>clock_timestamp() AND c.captured_at>clock_timestamp()-interval '30 seconds'
 ORDER BY c.captured_at DESC,c.id LIMIT 1;
 IF FOUND THEN
  SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO members_json FROM (
   SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_members WHERE scope_key=scope_value AND capture_id=saved.id
   UNION SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_accounting WHERE scope_key=scope_value AND capture_id=saved.id) x;
  checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
  IF checked->>'manifest'=saved.manifest AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') r WHERE (r->>'available')::boolean IS DISTINCT FROM true)
   AND public.opencitadel_analysis_authority(encoded,signature)=epoch THEN
   RETURN jsonb_build_object('watermark',saved.id,'authority_revision',epoch,'manifest',saved.manifest,'metrics',saved.body);
  END IF;
 END IF;
 capture_id:=public.gen_random_uuid();
 SELECT COALESCE(jsonb_agg(to_jsonb(r)),'[]'::jsonb) INTO members_json FROM (

SELECT r.run_id,r.formal_position,r.progress_position,r.observed_order,r.projection_revision,
 r.projector_version,r.as_of,r.completeness,
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
ORDER BY r.admitted_at,r.run_id LIMIT 100001

 ) r;
 IF jsonb_array_length(members_json)>100000 THEN RAISE EXCEPTION 'analysis_capacity_exceeded'; END IF;
 checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
 unavailable_coverage:=EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') c WHERE (c->>'available')::boolean IS DISTINCT FROM true);
 SELECT COALESCE(jsonb_agg(m),'[]'::jsonb) INTO members_json FROM jsonb_array_elements(members_json) m WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') c WHERE c->>'run_id'=m->>'run_id' AND (c->>'available')::boolean);
 INSERT INTO public.analysis_captures(id,scope_key,caller_id,query,authority_revision) VALUES(opencitadel_analysis_capture.capture_id,scope_value,actor,query_value,epoch) RETURNING captured_at INTO captured_at_value;
 INSERT INTO public.analysis_capture_members(capture_id,scope_key,run_id,ordinal,formal_position,progress_position,observed_order,projection_revision,projector_version,generation,coverage)
 SELECT opencitadel_analysis_capture.capture_id,scope_value,(m->>'run_id')::uuid,ordinality-1,(m->>'formal_position')::bigint,(m->>'progress_position')::bigint,(m->>'observed_order')::bigint,(m->>'projection_revision')::bigint,(m->>'projector_version')::integer,m->>'generation',m->'completeness' FROM jsonb_array_elements(members_json) WITH ORDINALITY AS x(m,ordinality);
 n:=public.opencitadel_analysis_accounting(encoded,signature,opencitadel_analysis_capture.capture_id);
 SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO members_json FROM (
 SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_members WHERE scope_key=scope_value AND capture_id=opencitadel_analysis_capture.capture_id
 UNION SELECT run_id,formal_position,progress_position,observed_order,projector_version,generation FROM public.analysis_capture_accounting WHERE scope_key=scope_value AND capture_id=opencitadel_analysis_capture.capture_id) x;
 checked:=public.opencitadel_analysis_manifest(encoded,signature,members_json);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(checked->'rows') r WHERE (r->>'available')::boolean IS DISTINCT FROM true) THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
 result:=jsonb_build_object('watermark',opencitadel_analysis_capture.capture_id,'authority_revision',epoch,'manifest',checked->>'manifest','captured_at',captured_at_value,'accounting_run_count',n,'coverage',CASE WHEN unavailable_coverage THEN 'partial_unavailable' ELSE 'complete' END);

 SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
WITH inputs AS(SELECT r.family,r.purpose,r.execution_mode,CASE WHEN q_configuration_revision IS NOT NULL THEN admission.id ELSE NULL::text END AS configuration_revision,r.status,r.admitted_at,r.terminal_at FROM public.analysis_capture_members m JOIN public.execution_view_runs r ON r.run_id=m.run_id AND r.scope_key=m.scope_key LEFT JOIN LATERAL(SELECT c.id FROM public.execution_configurations c WHERE c.scope_key=r.scope_key AND c.run_id=r.run_id AND c.body->>'stage'='admission' ORDER BY c.created_at,c.id LIMIT 1) admission ON true WHERE m.capture_id=opencitadel_analysis_capture.capture_id AND m.scope_key=scope_value)
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
 result:=result||jsonb_build_object('run_groups',section);

 SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
SELECT r.family,r.purpose,r.execution_mode,q_configuration_revision AS configuration_revision,CASE WHEN grain_value='hour' THEN date_trunc('hour',r.admitted_at AT TIME ZONE timezone_value) AT TIME ZONE 'UTC' - ((r.admitted_at AT TIME ZONE timezone_value)-(r.admitted_at AT TIME ZONE 'UTC')) ELSE date_trunc('day',r.admitted_at AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,sum(x.activity_occupancy_ms) AS activity_occupancy_ms,sum(x.activity_samples) AS activity_samples,sum(x.activity_missing) AS activity_missing,sum(x.tool_work_ms) AS tool_work_ms,sum(x.tool_samples) AS tool_samples,sum(x.tool_missing) AS tool_missing,sum(x.tool_terminal) AS tool_terminal,sum(x.tool_errors) AS tool_errors,sum(x.tool_excluded) AS tool_excluded,sum(x.tool_execution_errors) AS tool_execution_errors,sum(x.tool_business_errors) AS tool_business_errors,sum(x.tool_unknown) AS tool_unknown,sum(x.tool_deferred) AS tool_deferred,sum(x.tool_cancelled) AS tool_cancelled FROM (

WITH attempts AS (
 SELECT s.run_id,s.attempt_id,s.kind,s.status,s.business_outcome,
 greatest(s.started_at,r.admitted_at) AS left_at,least(s.ended_at,captured_at_value,r.terminal_at) AS right_at,
 s.started_at IS NOT NULL AND s.ended_at IS NOT NULL AND s.ended_at>=s.started_at AS valid
 FROM public.analysis_capture_members m JOIN public.execution_view_runs r ON r.scope_key=m.scope_key AND r.run_id=m.run_id
 JOIN public.execution_view_steps s ON s.scope_key=m.scope_key AND s.run_id=m.run_id
 WHERE m.capture_id=opencitadel_analysis_capture.capture_id AND m.scope_key=scope_value AND s.attempt_id IS NOT NULL AND s.status<>'queued'),
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

) x JOIN public.execution_view_runs r ON r.run_id=x.run_id AND r.scope_key=scope_value GROUP BY r.family,r.purpose,r.execution_mode,bucket
 ) x;
 result:=result||jsonb_build_object('intervals',section);

 SELECT COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb) INTO section FROM (
SELECT r.family,r.purpose,r.execution_mode,q_configuration_revision AS configuration_revision,CASE WHEN grain_value='hour' THEN date_trunc('hour',r.admitted_at AT TIME ZONE timezone_value) AT TIME ZONE 'UTC' - ((r.admitted_at AT TIME ZONE timezone_value)-(r.admitted_at AT TIME ZONE 'UTC')) ELSE date_trunc('day',r.admitted_at AT TIME ZONE timezone_value) AT TIME ZONE timezone_value END AS bucket,sum(x.approval_wait_ms) AS approval_wait_ms,sum(x.samples) AS samples,sum(x.missing) AS missing FROM (

WITH transitions AS (
 SELECT m.run_id,f->>'id' AS approval_id,o.occurred_at,f->'patch'->>'status' AS status
 FROM public.analysis_capture_members m JOIN public.execution_view_observations o ON o.scope_key=m.scope_key AND o.run_id=m.run_id
 CROSS JOIN LATERAL jsonb_array_elements(o.public_payload->'facts') f
 WHERE m.capture_id=opencitadel_analysis_capture.capture_id AND m.scope_key=scope_value AND o.projector_version=m.projector_version
 AND o.observed_order<=m.observed_order AND o.formal_position<=m.formal_position AND o.progress_position<=m.progress_position
 AND COALESCE((o.public_payload->>'applied')::boolean,true) AND f->>'kind'='approval'),
 raw_intervals AS (SELECT run_id,approval_id,min(occurred_at) FILTER(WHERE status IN ('waiting','requested','pending')) AS left_at,
 COALESCE(min(occurred_at) FILTER(WHERE status IN ('decided','expired','approved','rejected','cancelled')),captured_at_value) AS right_at FROM transitions GROUP BY run_id,approval_id),
 intervals AS (SELECT i.run_id,i.approval_id,CASE WHEN i.left_at IS NOT NULL THEN greatest(i.left_at,r.admitted_at) END AS left_at,least(i.right_at,captured_at_value,COALESCE(r.terminal_at,captured_at_value)) AS right_at FROM raw_intervals i JOIN public.execution_view_runs r ON r.scope_key=scope_value AND r.run_id=i.run_id),
 previous AS (SELECT *,max(right_at) OVER(PARTITION BY run_id ORDER BY left_at,right_at ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end FROM intervals WHERE left_at IS NOT NULL AND right_at>=left_at),
 totals AS (SELECT run_id,sum(extract(epoch FROM right_at-greatest(left_at,COALESCE(previous_end,left_at)))*1000) FILTER(WHERE previous_end IS NULL OR right_at>previous_end) AS duration FROM previous GROUP BY run_id)
SELECT i.run_id,max(t.duration) AS approval_wait_ms,count(*) FILTER(WHERE left_at IS NOT NULL AND right_at>=left_at) AS samples,
 count(*) FILTER(WHERE left_at IS NULL OR right_at<left_at) AS missing FROM intervals i LEFT JOIN totals t ON t.run_id=i.run_id GROUP BY i.run_id

) x JOIN public.execution_view_runs r ON r.run_id=x.run_id AND r.scope_key=scope_value GROUP BY r.family,r.purpose,r.execution_mode,bucket
 ) x;
 result:=result||jsonb_build_object('approvals',section);

 result:=result||jsonb_build_object('physical',public.opencitadel_analysis_usage(encoded,signature,opencitadel_analysis_capture.capture_id),'score_records',public.opencitadel_analysis_scores(encoded,signature,opencitadel_analysis_capture.capture_id),'allocations',public.opencitadel_analysis_allocations(encoded,signature,opencitadel_analysis_capture.capture_id));
 IF public.opencitadel_analysis_authority(encoded,signature)<>epoch THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
 RETURN result;
END $$
"""

ALLOCATIONS = r"""
CREATE FUNCTION public.opencitadel_analysis_allocations(encoded text, signature text, capture uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; saved jsonb; mode text; batch text; result jsonb;
BEGIN
 PERFORM public.opencitadel_analysis_authority(encoded,signature);
 SELECT scope_key,query INTO s,saved FROM public.analysis_captures WHERE id=capture AND caller_id=current_setting('app.user_id',true) AND expires_at>clock_timestamp();
 IF s IS NULL THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
 SELECT f->>1 INTO mode FROM jsonb_array_elements(saved->'filters') f WHERE f->>0='accounting';
 IF mode IS NULL OR mode='run' THEN RETURN '[]'::jsonb; END IF;
 SELECT f->>1 INTO batch FROM jsonb_array_elements(saved->'filters') f WHERE f->>0='batch_id';
 WITH batches AS(SELECT b.id,b.principal FROM public.evaluation_batches b WHERE b.scope_key=s AND
 ((mode='batch_total' AND b.id::text=batch) OR (mode='selected_result' AND EXISTS(SELECT 1 FROM public.analysis_capture_members m JOIN public.evaluation_batch_attempts a ON a.scope_key=s AND a.run_id=m.run_id JOIN public.evaluation_batch_results r ON r.scope_key=s AND r.id=a.result_id WHERE m.scope_key=s AND m.capture_id=capture AND r.batch_id=b.id)))),
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
    bind.execute(
        sa.text(
            "CREATE TABLE public.analysis_authority_epoch(singleton boolean PRIMARY KEY CHECK(singleton),revision bigint NOT NULL CHECK(revision>=0))"
        )
    )
    bind.execute(sa.text("INSERT INTO public.analysis_authority_epoch VALUES(true,0)"))
    bind.execute(sa.text("ALTER TABLE public.analysis_authority_epoch ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE public.analysis_authority_epoch FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"CREATE POLICY analysis_epoch_owner ON public.analysis_authority_epoch TO {quote(owner)} USING (true) WITH CHECK (true)"
        )
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON public.analysis_authority_epoch FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(r"""CREATE FUNCTION public.opencitadel_analysis_authority_bump() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'authority_truncate_forbidden'; END IF;
      UPDATE public.analysis_authority_epoch SET revision=revision+1 WHERE singleton;
      IF NOT FOUND THEN RAISE EXCEPTION 'analysis_authority_epoch_missing'; END IF;
      RETURN NULL;
    END $$""")
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_analysis_authority_bump() FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    for table, columns in [
        ("users", ("id", "status", "token_version", "global_role")),
        ("team_members", ("team_id", "user_id", "role")),
        ("teams", ("id",)),
    ]:
        bind.execute(
            sa.text(
                f"CREATE TRIGGER analysis_authority_identity AFTER INSERT OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_authority_bump()"
            )
        )
        changed = " OR ".join(f"OLD.{column} IS DISTINCT FROM NEW.{column}" for column in columns)
        bind.execute(
            sa.text(
                f"CREATE TRIGGER analysis_authority_update AFTER UPDATE ON public.{table} FOR EACH ROW WHEN ({changed}) EXECUTE FUNCTION public.opencitadel_analysis_authority_bump()"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE TRIGGER analysis_authority_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.opencitadel_analysis_authority_bump()"
            )
        )
    bind.execute(sa.text(AUTHORITY))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_authority(text,text) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_authority(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )

    bind.execute(
        sa.text(
            "CREATE FUNCTION public.opencitadel_analysis_capture_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'analysis_capture_immutable'; END $$"
        )
    )
    for table, ddl in {
        "analysis_captures": "id uuid PRIMARY KEY,scope_key text NOT NULL,caller_id text NOT NULL,query jsonb NOT NULL,authority_revision bigint NOT NULL,captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',UNIQUE(scope_key,id)",
        "analysis_capture_members": "capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<100000),formal_position bigint NOT NULL,progress_position bigint NOT NULL,observed_order bigint NOT NULL,projection_revision bigint NOT NULL,projector_version integer NOT NULL,generation text NOT NULL,coverage jsonb NOT NULL,PRIMARY KEY(capture_id,run_id),UNIQUE(capture_id,ordinal),FOREIGN KEY(scope_key,capture_id) REFERENCES analysis_captures(scope_key,id) ON DELETE CASCADE",
        "analysis_capture_accounting": "capture_id uuid NOT NULL,scope_key text NOT NULL,run_id uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<=1000000),formal_position bigint NOT NULL,progress_position bigint NOT NULL,observed_order bigint NOT NULL,projector_version integer NOT NULL,generation text NOT NULL,PRIMARY KEY(capture_id,run_id),UNIQUE(capture_id,ordinal),FOREIGN KEY(scope_key,capture_id) REFERENCES analysis_captures(scope_key,id) ON DELETE CASCADE",
        "analysis_capture_metrics": "capture_id uuid PRIMARY KEY,scope_key text NOT NULL,manifest text NOT NULL,body jsonb NOT NULL,FOREIGN KEY(scope_key,capture_id) REFERENCES analysis_captures(scope_key,id) ON DELETE CASCADE",
    }.items():
        bind.execute(sa.text(f"CREATE TABLE public.{table}({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(f"REVOKE ALL ON public.{table} FROM PUBLIC,{quote(api)},{quote(kernel)}")
        )
        valid = "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'"
        scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
        own = (
            "caller_id=current_setting('app.user_id',true)"
            if table == "analysis_captures"
            else "EXISTS(SELECT 1 FROM public.analysis_captures c WHERE c.id=capture_id AND c.caller_id=current_setting('app.user_id',true))"
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_capture_caller ON public.{table} TO {quote(api)},{quote(owner)} USING ({valid} AND {scoped} AND {own}) WITH CHECK ({valid} AND {scoped} AND {own})"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_capture_cleanup ON public.{table} TO {quote(owner)} USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')"
            )
        )
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON public.{table} TO {quote(api)}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER analysis_capture_immutable BEFORE UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_capture_immutable()"
            )
        )

    bind.execute(
        sa.text(
            "CREATE TABLE public.analysis_capture_slots(scope_key text NOT NULL,caller_id text NOT NULL,revision bigint NOT NULL DEFAULT 0,PRIMARY KEY(scope_key,caller_id))"
        )
    )
    bind.execute(sa.text("ALTER TABLE public.analysis_capture_slots ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE public.analysis_capture_slots FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"CREATE POLICY analysis_slot_owner ON public.analysis_capture_slots TO {quote(owner)} USING(true) WITH CHECK(true)"
        )
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON public.analysis_capture_slots FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX analysis_capture_expiry ON public.analysis_captures(scope_key,caller_id,expires_at)"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_analysis_capture_capacity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF NEW.caller_id IS DISTINCT FROM current_setting('app.user_id',true) OR NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
      OR NEW.expires_at>clock_timestamp()+interval '15 minutes' THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
      INSERT INTO public.analysis_capture_slots(scope_key,caller_id,revision) VALUES(NEW.scope_key,NEW.caller_id,1) ON CONFLICT(scope_key,caller_id) DO UPDATE SET revision=public.analysis_capture_slots.revision+1;
      -- A real row update serializes RC captures and makes stale RR writers retry, unlike an advisory lock alone.
      DELETE FROM public.analysis_captures WHERE scope_key=NEW.scope_key AND caller_id=NEW.caller_id AND expires_at<=clock_timestamp();
      IF (SELECT count(*) FROM public.analysis_captures WHERE scope_key=NEW.scope_key AND caller_id=NEW.caller_id)>=20 THEN
        DELETE FROM public.analysis_captures WHERE id=(SELECT id FROM public.analysis_captures WHERE scope_key=NEW.scope_key AND caller_id=NEW.caller_id ORDER BY captured_at,id LIMIT 1);
      END IF;
      RETURN NEW;
    END $$""")
    )
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_analysis_capture_capacity() FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER analysis_capture_capacity BEFORE INSERT ON public.analysis_captures FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_capture_capacity()"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_analysis_cleanup(batch_limit integer) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE removed integer; BEGIN
    IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'system' OR current_setting('app.system_actor',true) IS DISTINCT FROM 'execution-kernel' OR batch_limit NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
    WITH expired AS(SELECT id FROM public.analysis_captures WHERE expires_at<=clock_timestamp() ORDER BY expires_at,id LIMIT batch_limit FOR UPDATE SKIP LOCKED), removed_rows AS(DELETE FROM public.analysis_captures c USING expired e WHERE c.id=e.id RETURNING c.id) SELECT count(*) INTO removed FROM removed_rows;
    RETURN removed; END $$""")
    )
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_analysis_cleanup(integer) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_cleanup(integer) TO {quote(kernel)}"
        )
    )

    valid = (
        "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'"
    )
    scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
    for table in (
        "execution_model_settlements",
        "execution_usage_publications",
        "execution_configurations",
    ):
        bind.execute(
            sa.text(
                f"CREATE POLICY analysis_fact_reader ON public.{table} FOR SELECT TO {quote(owner)} USING ({valid} AND {scoped})"
            )
        )
    bind.execute(sa.text(PRIVATE_FACTS))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_usage(text,text,uuid) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_usage(text,text,uuid) TO {quote(api)}"
        )
    )

    bind.execute(sa.text(SCORE_FACTS))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_scores(text,text,uuid) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_scores(text,text,uuid) TO {quote(api)}"
        )
    )

    bind.execute(sa.text(ACCOUNTING))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_accounting(text,text,uuid) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_accounting(text,text,uuid) TO {quote(api)}"
        )
    )
    bind.execute(sa.text(f"REVOKE INSERT ON public.analysis_capture_accounting FROM {quote(api)}"))

    bind.execute(sa.text(JUDGE_RESOURCES))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_judge_resources(text,text,jsonb) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_judge_resources(text,text,jsonb) TO {quote(api)}"
        )
    )

    # Retain only fixed identities so deleting a pin cannot silently shrink a new manifest.
    bind.execute(
        sa.text(
            "CREATE TABLE public.analysis_required_pins(scope_key text NOT NULL,owner_kind text NOT NULL,owner_id text NOT NULL,resource_kind text NOT NULL,resource_id text NOT NULL,resource_version text NOT NULL,PRIMARY KEY(scope_key,owner_kind,owner_id,resource_kind,resource_id,resource_version))"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO public.analysis_required_pins SELECT scope_key,owner_kind,owner_id,resource_kind,resource_id,resource_version FROM public.resource_pins"
        )
    )
    bind.execute(sa.text("ALTER TABLE public.analysis_required_pins ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE public.analysis_required_pins FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"CREATE POLICY analysis_required_pin_owner ON public.analysis_required_pins TO {quote(owner)} USING(true) WITH CHECK(true)"
        )
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON public.analysis_required_pins FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_analysis_required_pin() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      INSERT INTO public.analysis_required_pins VALUES(NEW.scope_key,NEW.owner_kind,NEW.owner_id,NEW.resource_kind,NEW.resource_id,NEW.resource_version) ON CONFLICT DO NOTHING;
      RETURN NULL; END $$""")
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_analysis_required_pin() FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER analysis_required_pin AFTER INSERT OR UPDATE ON public.resource_pins FOR EACH ROW EXECUTE FUNCTION public.opencitadel_analysis_required_pin()"
        )
    )

    bind.execute(sa.text(RESOURCE_AVAILABILITY))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_resources_available(text,text,jsonb) FROM PUBLIC"
        )
    )

    # Legacy object-backed case resource lists cannot be certified from surviving pins.
    bind.execute(
        sa.text(
            "CREATE TABLE public.analysis_dataset_pin_coverage(scope_key text NOT NULL,version_id uuid NOT NULL,PRIMARY KEY(scope_key,version_id))"
        )
    )
    bind.execute(
        sa.text("ALTER TABLE public.analysis_dataset_pin_coverage ENABLE ROW LEVEL SECURITY")
    )
    bind.execute(
        sa.text("ALTER TABLE public.analysis_dataset_pin_coverage FORCE ROW LEVEL SECURITY")
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY analysis_dataset_coverage_owner ON public.analysis_dataset_pin_coverage TO {quote(owner)} USING(true) WITH CHECK(true)"
        )
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON public.analysis_dataset_pin_coverage FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(r"""CREATE FUNCTION public.opencitadel_analysis_certify_dataset(encoded text, signature text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE p jsonb; s text; version uuid; expected jsonb; actual jsonb;
    BEGIN
      PERFORM public.opencitadel_analysis_authority(encoded,signature);
      p:=encoded::jsonb; s:=p->>'scope'; version:=(p->>'version_id')::uuid;
      IF p->>'operation' IS DISTINCT FROM 'certify_dataset' OR jsonb_typeof(p->'resources') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'resources')>100000 THEN RAISE EXCEPTION 'analysis_authorization_invalid'; END IF;
      SELECT COALESCE(jsonb_agg(jsonb_build_array(c.id,c.object_id,c.object_index,o.digest) ORDER BY c.id),'[]'::jsonb) INTO actual
      FROM public.evaluation_version_cases m JOIN public.evaluation_case_revisions c ON c.scope_key=m.scope_key AND c.id=m.case_revision_id JOIN public.evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id AND o.cleaned_at IS NULL
      WHERE m.scope_key=s AND m.version_id=version;
      IF actual IS DISTINCT FROM p->'members' OR NOT EXISTS(SELECT 1 FROM public.evaluation_dataset_versions v WHERE v.scope_key=s AND v.id=version) THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
      IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'resources') r WHERE NOT EXISTS(SELECT 1 FROM public.resource_pins pin WHERE pin.scope_key=s AND pin.owner_kind='dataset_version' AND pin.owner_id=version::text AND pin.resource_kind=r->>'resource_kind' AND pin.resource_id=r->>'resource_id' AND pin.resource_version=r->>'resource_version' AND pin.available)) THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
      IF NOT public.opencitadel_analysis_resources_available(encoded,signature,p->'resources') THEN RAISE EXCEPTION 'analysis_refresh_required'; END IF;
      INSERT INTO public.analysis_required_pins SELECT s,'dataset_version',version::text,r->>'resource_kind',r->>'resource_id',r->>'resource_version' FROM jsonb_array_elements(p->'resources') r ON CONFLICT DO NOTHING;
      INSERT INTO public.analysis_dataset_pin_coverage VALUES(s,version) ON CONFLICT DO NOTHING;
      RETURN true;
    END $$""")
    )
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_certify_dataset(text,text) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_certify_dataset(text,text) TO {quote(api)}"
        )
    )

    bind.execute(sa.text(CAPTURE_MANIFEST))
    bind.execute(sa.text(CAPTURE))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_manifest(text,text,jsonb) FROM PUBLIC"
        )
    )
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_analysis_capture(text,text) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_analysis_capture(text,text) TO {quote(api)}"
        )
    )
    for table in (
        "analysis_captures",
        "analysis_capture_members",
        "analysis_capture_accounting",
        "analysis_capture_metrics",
    ):
        bind.execute(sa.text(f"REVOKE ALL ON public.{table} FROM {quote(api)}"))
    for function_name, arguments in [
        ("opencitadel_analysis_usage", "text,text,uuid"),
        ("opencitadel_analysis_scores", "text,text,uuid"),
        ("opencitadel_analysis_accounting", "text,text,uuid"),
        ("opencitadel_analysis_judge_resources", "text,text,jsonb"),
    ]:
        bind.execute(
            sa.text(f"REVOKE ALL ON FUNCTION public.{function_name}({arguments}) FROM {quote(api)}")
        )

    bind.execute(sa.text(ALLOCATIONS))
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_allocations(text,text,uuid) FROM PUBLIC"
        )
    )


def downgrade():
    raise RuntimeError("execution analysis authority downgrade is unsupported")
