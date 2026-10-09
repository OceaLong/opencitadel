"""Bounded caller-bound safe evaluation read snapshots; no private API table grants."""

import sqlalchemy as sa

from alembic import op

revision = "0014evaluation_summary"
down_revision = "0013evaluation_reviews"
branch_labels = None
depends_on = None

FUNCTION = r"""
CREATE FUNCTION public.opencitadel_e11_snapshot(encoded text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; principal jsonb; actor text; team text; s text; secret text; mac bytea;
supplied bytea; difference integer:=0; i integer; target uuid; chosen_cut bigint; latest bigint;
snapshot uuid; evicted uuid; captured timestamptz; result jsonb; saved record;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
 OR encoded IS NULL OR octet_length(encoded)>65536 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'summary_authorization_invalid'; END IF;
 SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
 mac:=public.hmac(convert_to('opencitadel:e11:snapshot:v1:'||encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256'); supplied:=decode(signature,'hex');
 FOR i IN 0..31 LOOP difference:=difference | (get_byte(mac,i) # get_byte(supplied,i)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'summary_authorization_invalid'; END IF;
 p:=encoded::jsonb; principal:=p->'principal'; actor:=principal->>'user_id'; team:=NULLIF(current_setting('app.team_id',true),'');
 s:=CASE WHEN team IS NULL THEN 'user:'||actor ELSE 'team:'||team END;
 IF actor IS DISTINCT FROM current_setting('app.user_id',true) OR p->>'scope' IS DISTINCT FROM s
 OR p->>'authorization_signature' IS DISTINCT FROM current_setting('app.auth_signature',true)
 OR (p->>'expires')::numeric < extract(epoch FROM clock_timestamp())
 THEN RAISE EXCEPTION 'summary_authorization_invalid'; END IF;
 PERFORM id FROM public.users WHERE id=actor FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active' AND token_version=(principal->>'token_version')::integer AND global_role=principal->>'global_role')
 THEN RAISE EXCEPTION 'summary_authorization_revoked'; END IF;
 IF team IS NOT NULL THEN
  PERFORM user_id FROM public.team_members WHERE user_id=actor AND team_id=team FOR SHARE;
  IF NOT EXISTS(SELECT 1 FROM public.team_members WHERE user_id=actor AND team_id=team AND role=principal->'team_roles'->>team) THEN RAISE EXCEPTION 'summary_authorization_revoked'; END IF;
 END IF;
 target:=(p->>'batch_id')::uuid;
 PERFORM id FROM public.evaluation_batches WHERE scope_key=s AND id=target FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'summary_not_found'; END IF;
 IF p->>'operation'='invalidations' THEN
  chosen_cut:=(p->>'evaluation_revision')::bigint;
  IF chosen_cut<0 OR chosen_cut>COALESCE((SELECT revision FROM public.evaluation_score_heads WHERE scope_key=s AND batch_id=target),0) THEN RAISE EXCEPTION 'summary_revision_unavailable'; END IF;
  RETURN COALESCE((SELECT jsonb_agg(jsonb_build_object('intent_id',intent_id,'run_id',run_id,'source_set_id',source_set_id,'evaluation_revision',evaluation_revision)) FROM public.evaluation_judge_invalidations WHERE scope_key=s AND batch_id=target AND evaluation_revision<=chosen_cut),'[]'::jsonb);
 END IF;
 IF p->>'operation'='get' THEN
  SELECT * INTO saved FROM public.evaluation_summary_snapshots WHERE scope_key=s AND caller_id=actor AND batch_id=target AND id=(p->>'snapshot_id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'summary_expired'; END IF;
  IF saved.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'summary_expired'; END IF;
  RETURN saved.body;
 END IF;
 IF p->>'operation'<>'capture' OR p->>'source' NOT IN ('human','model','rule') OR length(p->>'dimension') NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'summary_query_invalid'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.evaluation_suite_versions v JOIN public.evaluation_batches b ON b.scope_key=v.scope_key AND b.suite_version=v.id WHERE b.scope_key=s AND b.id=target AND v.rubric_version=(p->>'rubric_id')::uuid)
 AND NOT EXISTS(SELECT 1 FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.batch_id=target AND j.rubric_id=(p->>'rubric_id')::uuid)
 THEN RAISE EXCEPTION 'summary_query_invalid'; END IF;
 -- Serialize captures per caller/batch, bound growth, remove only that caller's expired cache.
 PERFORM pg_advisory_xact_lock(hashtextextended('e11:'||s||':'||actor||':'||target::text,0));
 DELETE FROM public.evaluation_summary_snapshots WHERE scope_key=s AND caller_id=actor AND batch_id=target AND expires_at<=clock_timestamp();
 IF (SELECT count(*) FROM public.evaluation_summary_snapshots WHERE scope_key=s AND caller_id=actor AND batch_id=target)>=20 THEN
  SELECT id INTO evicted FROM public.evaluation_summary_snapshots WHERE scope_key=s AND caller_id=actor AND batch_id=target ORDER BY expires_at,id LIMIT 1;
  PERFORM set_config('app.e11_eviction',evicted::text,true);
  DELETE FROM public.evaluation_summary_snapshots WHERE id=evicted AND scope_key=s AND caller_id=actor AND batch_id=target;
  PERFORM set_config('app.e11_eviction','',true);
 END IF;
 captured:=clock_timestamp(); snapshot:=public.gen_random_uuid();
 -- Every mutable usage observation, score cut and metadata row is captured in this one SQL statement.
 WITH head AS (SELECT COALESCE((SELECT revision FROM public.evaluation_score_heads WHERE scope_key=s AND batch_id=target),0) AS revision),
 selected_cut AS (SELECT COALESCE((p->>'evaluation_revision')::bigint,revision) AS revision,revision AS latest FROM head),
 members AS (SELECT r.*,c.case_key,cv.name AS config_name,a.run_id,a.run_revision
 FROM public.evaluation_batch_results r JOIN public.evaluation_case_revisions c ON c.scope_key=r.scope_key AND c.id=r.case_revision_id
 JOIN public.evaluation_config_versions cv ON cv.scope_key=r.scope_key AND cv.id=r.config_version_id
 LEFT JOIN public.evaluation_batch_attempts a ON a.scope_key=r.scope_key AND a.result_id=r.id AND a.attempt=r.attempt
 WHERE r.scope_key=s AND r.batch_id=target ORDER BY r.ordinal LIMIT 5000),
 score_heads AS (SELECT DISTINCT ON(v.result_id) v.result_id,v.id AS set_id,v.evaluation_revision,v.run_id AS score_run_id,v.run_revision AS score_run_revision,v.result_revision AS score_result_revision,q.value,q.status
 FROM public.evaluation_score_sets v JOIN public.evaluation_scores q ON q.scope_key=v.scope_key AND q.set_id=v.id
 CROSS JOIN selected_cut cut
 WHERE v.scope_key=s AND v.batch_id=target AND v.evaluation_revision<=cut.revision AND v.source=p->>'source' AND v.rubric_revision=(p->>'rubric_id')::uuid AND q.dimension=p->>'dimension'
 ORDER BY v.result_id,v.evaluation_revision DESC),
 linked_runs AS (
 SELECT a.result_id,a.run_id,'subject'::text AS purpose FROM public.evaluation_batch_attempts a JOIN members m ON m.id=a.result_id WHERE a.scope_key=s
 UNION SELECT j.result_id,j.run_id,'judge' FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.batch_id=target),
 physical AS (SELECT DISTINCT d.call_identity,l.result_id,l.purpose,r.state,r.settlement
 FROM linked_runs l JOIN public.execution_model_dispatches d ON d.scope_key=s AND d.run_id=l.run_id
 LEFT JOIN public.evaluation_budget_reservations r ON r.scope_key=s AND r.call_identity::text=d.call_identity AND r.demand->>'batch_id'=target::text),
 usage AS (SELECT result_id,purpose,count(*) AS calls,count(*) FILTER(WHERE settlement->>'tokens' IS NOT NULL) AS token_known,
 count(*) FILTER(WHERE settlement->>'money' IS NOT NULL) AS money_known,
 sum((settlement->>'tokens')::bigint) AS tokens,sum((settlement->>'money')::numeric)::text AS money,
 count(*) FILTER(WHERE state IS DISTINCT FROM 'settled') AS unresolved FROM physical GROUP BY result_id,purpose),
 invalid AS (SELECT source_set_id,evaluation_revision FROM public.evaluation_judge_invalidations CROSS JOIN selected_cut cut
 WHERE scope_key=s AND batch_id=target AND evaluation_revision<=cut.revision),
 rows AS (SELECT jsonb_build_object('id',m.id,'case_id',m.case_revision_id,'case_label',m.case_key,'config_id',m.config_version_id,'config_label',m.config_name,
 'repetition',m.repetition,'attempt',m.attempt,'run_id',m.run_id,'run_revision',m.run_revision,'result_revision',m.revision,'execution_status',m.execution_status,'scoring_status',m.scoring_status,
 'attempts',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt',a.attempt,'run_id',a.run_id,'run_revision',a.run_revision,'status',a.status) ORDER BY a.attempt) FROM public.evaluation_batch_attempts a WHERE a.scope_key=s AND a.result_id=m.id),'[]'::jsonb),
 'score_run_id',h.score_run_id,'score_run_revision',h.score_run_revision,'score_result_revision',h.score_result_revision,
 'value',CASE WHEN h.status='valid' AND NOT EXISTS(SELECT 1 FROM invalid x WHERE x.source_set_id=h.set_id) THEN h.value ELSE NULL END,
 'invalidated',EXISTS(SELECT 1 FROM invalid x WHERE x.source_set_id=h.set_id),
 'subject_usage',(SELECT to_jsonb(u)-'result_id'-'purpose' FROM usage u WHERE u.result_id=m.id AND u.purpose='subject'),
 'judge_usage',(SELECT to_jsonb(u)-'result_id'-'purpose' FROM usage u WHERE u.result_id=m.id AND u.purpose='judge')) AS body,m.ordinal
 FROM members m LEFT JOIN score_heads h ON h.result_id=m.id)
 SELECT jsonb_build_object('id',snapshot,'batch_id',target,'captured_at',captured,'usage_watermark',captured,'expires_at',captured+interval '15 minutes',
 'evaluation_revision',cut.revision,'source',p->>'source','dimension',p->>'dimension','rubric_id',p->>'rubric_id','rows',COALESCE((SELECT jsonb_agg(body ORDER BY ordinal) FROM rows),'[]'::jsonb),
 'allocations',COALESCE((SELECT jsonb_agg(jsonb_build_object('kind',CASE WHEN n.id=target THEN 'original' ELSE 'additional' END,'token_budget',n.body->'token_budget','money_budget',n.body->'money_budget','authorizer',CASE WHEN n.id=target THEN b.principal->>'user_id' ELSE j.authorizer->>'user_id' END,'intent_id',j.id))
 FROM public.evaluation_budget_namespaces n JOIN public.evaluation_batches b ON b.scope_key=s AND b.id=target
 LEFT JOIN public.evaluation_judge_intents j ON j.scope_key=s AND j.namespace_id=n.id AND j.batch_id=target AND j.rescore IS NOT NULL
 WHERE n.scope_key=s AND (n.id=target OR j.id IS NOT NULL)),'[]'::jsonb)),cut.revision,cut.latest INTO result,chosen_cut,latest FROM selected_cut cut;
 IF chosen_cut<0 OR chosen_cut>latest THEN RAISE EXCEPTION 'summary_revision_unavailable'; END IF;
 INSERT INTO public.evaluation_summary_snapshots(id,scope_key,caller_id,batch_id,expires_at,body) VALUES(snapshot,s,actor,target,captured+interval '15 minutes',result);
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
            "CREATE TABLE evaluation_summary_snapshots(id uuid PRIMARY KEY,scope_key text NOT NULL,caller_id text NOT NULL,batch_id uuid NOT NULL,expires_at timestamptz NOT NULL,body jsonb NOT NULL,FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id))"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX e11_snapshot_expiry ON evaluation_summary_snapshots(scope_key,caller_id,batch_id,expires_at)"
        )
    )
    valid = (
        "public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'"
    )
    scoped = "scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
    bind.execute(sa.text("ALTER TABLE evaluation_summary_snapshots ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE evaluation_summary_snapshots FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON evaluation_summary_snapshots FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e11_capture_owner ON evaluation_summary_snapshots TO {quote(owner)} USING ({valid} AND {scoped} AND caller_id=current_setting('app.user_id',true)) WITH CHECK ({valid} AND {scoped} AND caller_id=current_setting('app.user_id',true))"
        )
    )
    for table in (
        "evaluation_judge_intents",
        "evaluation_judge_invalidations",
        "evaluation_budget_reservations",
        "evaluation_budget_namespaces",
        "execution_model_dispatches",
    ):
        bind.execute(
            sa.text(
                f"CREATE POLICY e11_read_owner ON {table} FOR SELECT TO {quote(owner)} USING ({valid} AND {scoped})"
            )
        )
    bind.execute(
        sa.text(
            "CREATE FUNCTION public.opencitadel_e11_snapshot_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN IF TG_OP='UPDATE' OR (OLD.expires_at>clock_timestamp() AND OLD.id::text IS DISTINCT FROM current_setting('app.e11_eviction',true)) THEN RAISE EXCEPTION 'summary_snapshot_immutable'; END IF; RETURN OLD; END $$"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e11_snapshot_immutable BEFORE UPDATE OR DELETE ON evaluation_summary_snapshots FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e11_snapshot_guard()"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e11_cleanup_owner ON evaluation_summary_snapshots TO {quote(owner)} USING (public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_e11_cleanup(batch_limit integer) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE removed integer; BEGIN
    IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'system' OR current_setting('app.system_actor',true) IS DISTINCT FROM 'execution-kernel' OR batch_limit NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'summary_authorization_invalid'; END IF;
    WITH expired AS (SELECT id FROM public.evaluation_summary_snapshots WHERE expires_at<=clock_timestamp() ORDER BY expires_at,id LIMIT batch_limit FOR UPDATE SKIP LOCKED), deleted AS (DELETE FROM public.evaluation_summary_snapshots s USING expired e WHERE s.id=e.id RETURNING s.id) SELECT count(*) INTO removed FROM deleted;
    RETURN removed; END $$""")
    )
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_e11_cleanup(integer) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e11_cleanup(integer) TO {quote(kernel)}"
        )
    )
    bind.execute(sa.text(FUNCTION))
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_e11_snapshot(text,text) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e11_snapshot(text,text) TO {quote(api)}"
        )
    )


def downgrade():
    raise RuntimeError("evaluation read projection requires forward migration")
