"""Authenticated human commands and durable kernel rescore/cancel requests."""

import sqlalchemy as sa

from alembic import op

revision = "0013evaluation_reviews"
down_revision = "0012evaluation_judges"
branch_labels = None
depends_on = None

SCOPE = """owner_user_id varchar(255),team_id varchar(255),scope_key varchar(261) GENERATED ALWAYS AS
(CASE WHEN owner_user_id IS NOT NULL THEN 'user:'||owner_user_id ELSE 'team:'||team_id END) STORED,
created_by varchar(255) NOT NULL,created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
CHECK ((owner_user_id IS NULL) != (team_id IS NULL))"""

COMMAND = r"""
CREATE FUNCTION public.opencitadel_e09_command(encoded text,signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; principal jsonb; original jsonb; source_actor text; s text; actor text; team text; owner text; secret text;
mac bytea; supplied bytea; difference integer:=0; i integer;
r record; b record; a record; prior record; v bigint; set_id uuid; score jsonb; predecessor uuid;
rubric uuid; required jsonb; applicable jsonb; receipt jsonb; review text; command_id uuid;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
   OR current_setting('app.is_auditor',true) IS DISTINCT FROM 'false'
   OR encoded IS NULL OR octet_length(encoded)>1048576 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'review_authorization_invalid'; END IF;
 SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
 mac:=public.hmac(convert_to('opencitadel:e09:command:v1:'||encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256');
 supplied:=decode(signature,'hex');
 FOR i IN 0..31 LOOP difference:=difference | (get_byte(mac,i) # get_byte(supplied,i)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'review_authorization_invalid'; END IF;
 p:=encoded::jsonb; principal:=p->'principal'; actor:=principal->>'user_id';
 team:=NULLIF(current_setting('app.team_id',true),''); owner:=CASE WHEN team IS NULL THEN actor ELSE NULL END;
 s:=CASE WHEN team IS NULL THEN 'user:'||actor ELSE 'team:'||team END;
 IF actor IS DISTINCT FROM current_setting('app.user_id',true) OR p->>'scope' IS DISTINCT FROM s
   OR p->>'request_id' IS DISTINCT FROM current_setting('app.request_id',true)
   OR p->>'kind' NOT IN ('human','rescore','cancel') OR length(trim(p->>'request_id')) NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'review_authorization_invalid'; END IF;
 PERFORM id FROM public.users WHERE id=actor FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active' AND token_version=(principal->>'token_version')::integer AND global_role=principal->>'global_role' AND global_role<>'auditor')
 THEN RAISE EXCEPTION 'review_authorization_revoked'; END IF;
 IF team IS NOT NULL THEN
   PERFORM user_id FROM public.team_members WHERE user_id=actor AND team_id=team FOR SHARE;
   IF NOT EXISTS(SELECT 1 FROM public.team_members WHERE user_id=actor AND team_id=team AND role=principal->'team_roles'->>team)
   THEN RAISE EXCEPTION 'review_authorization_revoked'; END IF;
 END IF;
 SELECT * INTO r FROM public.evaluation_batch_results WHERE scope_key=s AND id=(p->>'result_id')::uuid;
 IF NOT FOUND THEN RAISE EXCEPTION 'review_not_found'; END IF;
 SELECT * INTO b FROM public.evaluation_batches WHERE scope_key=s AND id=r.batch_id FOR UPDATE;
 SELECT * INTO r FROM public.evaluation_batch_results WHERE scope_key=s AND id=r.id FOR UPDATE;
 SELECT * INTO prior FROM public.evaluation_review_commands WHERE scope_key=s AND result_id=r.id AND kind=p->>'kind' AND request_id=p->>'request_id';
 IF FOUND THEN
   IF prior.fingerprint IS DISTINCT FROM p->>'fingerprint' OR prior.created_by<>actor THEN RAISE EXCEPTION 'review_revision_conflict'; END IF;
   RETURN prior.receipt;
 END IF;
 IF p->>'kind'='rescore' THEN
   PERFORM set_config('app.e09_source_batch',b.id::text,true);
   original:=p->'source_principal'; source_actor:=original->>'user_id';
   IF original IS DISTINCT FROM b.principal THEN RAISE EXCEPTION 'review_authorization_invalid'; END IF;
   PERFORM id FROM public.users WHERE id=source_actor FOR SHARE;
   IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=source_actor AND status='active' AND token_version=(original->>'token_version')::integer AND global_role=original->>'global_role' AND global_role<>'auditor') THEN RAISE EXCEPTION 'review_authorization_revoked'; END IF;
   IF team IS NOT NULL THEN
     PERFORM user_id FROM public.team_members WHERE user_id=source_actor AND team_id=team FOR SHARE;
     IF NOT EXISTS(SELECT 1 FROM public.team_members WHERE user_id=source_actor AND team_id=team AND role=original->'team_roles'->>team) THEN RAISE EXCEPTION 'review_authorization_revoked'; END IF;
   END IF;
   PERFORM c.content_id FROM public.execution_public_content c WHERE c.scope_key=s AND p->'source_content' ? c.content_id::text FOR SHARE;
   IF jsonb_typeof(p->'source_content') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'source_content')=0 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p->'source_content') wanted WHERE NOT EXISTS(SELECT 1 FROM public.execution_public_content c JOIN public.execution_content_bindings binding ON binding.scope_key=c.scope_key AND binding.content_id=c.content_id WHERE c.scope_key=s AND c.content_id::text=wanted AND NOT c.redacted AND binding.run_id=(p->'candidate'->>'run_id')::uuid AND binding.phase='output')) THEN RAISE EXCEPTION 'review_source_unavailable'; END IF;
 END IF;
 SELECT * INTO a FROM public.evaluation_batch_attempts WHERE scope_key=s AND result_id=r.id AND attempt=r.attempt;
 IF r.revision<>(p->>'expected_result_revision')::bigint
   OR COALESCE((SELECT revision FROM public.evaluation_score_heads WHERE scope_key=s AND batch_id=b.id),0)<>(p->>'expected_revision')::bigint
 THEN RAISE EXCEPTION 'review_revision_conflict'; END IF;
 IF p->>'kind'<>'cancel' AND (r.execution_status<>'succeeded' OR r.unknown_effect OR r.recovery_pending
   OR a.run_id IS DISTINCT FROM (p->'candidate'->>'run_id')::uuid OR a.run_revision<>(p->'candidate'->>'run_revision')::bigint)
 THEN RAISE EXCEPTION 'review_result_unavailable'; END IF;
 IF p->>'kind'<>'cancel' THEN
   PERFORM n.id FROM public.evaluation_budget_namespaces n JOIN public.evaluation_budget_bindings binding ON binding.scope_key=n.scope_key AND binding.namespace_id=n.id
     WHERE binding.scope_key=s AND binding.run_id=a.run_id FOR UPDATE OF n;
   IF public.opencitadel_e06_effect_unsafe(s,a.run_id,true,p->>'effect_proof',p->>'effect_signature') THEN RAISE EXCEPTION 'review_result_unavailable'; END IF;
 END IF;
 rubric:=(p->>'rubric_version')::uuid;
 IF p->>'kind'='human' THEN
   IF rubric<>(SELECT rubric_version FROM public.evaluation_suite_versions WHERE scope_key=s AND id=b.suite_version)
    AND NOT EXISTS(SELECT 1 FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.result_id=r.id AND j.rubric_id=rubric AND j.rescore IS NOT NULL AND j.candidate->>'run_id'=a.run_id::text)
   THEN RAISE EXCEPTION 'review_rubric_unavailable'; END IF;
   INSERT INTO public.evaluation_review_requirements(batch_id,suite_id,rubric_id,requirements,owner_user_id,team_id,created_by) SELECT b.id,b.suite_version,sv.rubric_version,p->'batch_required',owner,team,actor FROM public.evaluation_suite_versions sv WHERE sv.scope_key=s AND sv.id=b.suite_version ON CONFLICT DO NOTHING;
   required:=p->'required'; applicable:=p->'applicable';
   INSERT INTO public.evaluation_score_heads(batch_id,revision,owner_user_id,team_id,created_by) VALUES(b.id,0,owner,team,actor) ON CONFLICT DO NOTHING;
   UPDATE public.evaluation_score_heads SET revision=revision+1 WHERE scope_key=s AND batch_id=b.id AND revision=(p->>'expected_revision')::bigint RETURNING revision INTO v;
   IF v IS NULL THEN RAISE EXCEPTION 'review_revision_conflict'; END IF;
   set_id:=public.gen_random_uuid();
   INSERT INTO public.evaluation_score_sets(id,batch_id,result_id,result_revision,run_id,run_revision,evaluation_revision,rubric_revision,source,request_id,fingerprint,status,required_dimensions,applicable_dimensions,owner_user_id,team_id,created_by)
    VALUES(set_id,b.id,r.id,r.revision,a.run_id,a.run_revision,v,rubric,'human',p->>'request_id',p->>'fingerprint','complete',required,applicable,owner,team,actor);
   FOR score IN SELECT * FROM jsonb_array_elements(p->'scores') LOOP
     IF NOT applicable ? (score->>'dimension') THEN RAISE EXCEPTION 'review_dimension_unavailable'; END IF;
     SELECT q.id INTO predecessor FROM public.evaluation_scores q JOIN public.evaluation_score_sets ss ON ss.scope_key=q.scope_key AND ss.id=q.set_id
       WHERE ss.scope_key=s AND ss.result_id=r.id AND ss.rubric_revision=rubric AND ss.source='human' AND q.dimension=score->>'dimension' ORDER BY ss.evaluation_revision DESC LIMIT 1;
     IF score->>'supersedes_id' IS NOT NULL THEN
       IF predecessor IS DISTINCT FROM (score->>'supersedes_id')::uuid THEN RAISE EXCEPTION 'review_supersedes_mismatch'; END IF;
     END IF;
     INSERT INTO public.evaluation_scores(id,set_id,dimension,status,value,reason,evidence,supersedes_id,owner_user_id,team_id,created_by)
      VALUES(public.gen_random_uuid(),set_id,score->>'dimension',score->>'status',NULLIF(score->'value','null'::jsonb),score->>'reason',score->'evidence',predecessor,owner,team,actor);
   END LOOP;
   SELECT CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements_text(required) d WHERE NOT EXISTS(
     SELECT 1 FROM (SELECT DISTINCT ON(q.dimension) q.dimension,q.status FROM public.evaluation_scores q JOIN public.evaluation_score_sets ss ON ss.scope_key=q.scope_key AND ss.id=q.set_id
       WHERE ss.scope_key=s AND ss.result_id=r.id AND ss.rubric_revision=rubric AND ss.source='human' ORDER BY q.dimension,ss.evaluation_revision DESC) latest WHERE latest.dimension=d AND latest.status='valid'))
    THEN 'pending' ELSE 'complete' END INTO review;
   INSERT INTO public.evaluation_review_states(result_id,batch_id,rubric_id,status,required_dimensions,applicable_dimensions,evaluation_revision,owner_user_id,team_id,created_by)
    VALUES(r.id,b.id,rubric,review,required,applicable,v,owner,team,actor)
    ON CONFLICT(scope_key,result_id,rubric_id) DO UPDATE SET status=excluded.status,evaluation_revision=excluded.evaluation_revision;
   UPDATE public.evaluation_batch_results SET revision=revision+1 WHERE scope_key=s AND id=r.id;
   UPDATE public.evaluation_batches SET review_status=CASE WHEN EXISTS(
     SELECT 1 FROM public.evaluation_batch_results other WHERE other.scope_key=s AND other.batch_id=b.id
       AND COALESCE((SELECT rs.status FROM public.evaluation_review_states rs WHERE rs.scope_key=s AND rs.result_id=other.id ORDER BY rs.evaluation_revision DESC LIMIT 1),
           CASE WHEN COALESCE(((SELECT facts.requirements FROM public.evaluation_review_requirements facts WHERE facts.scope_key=s AND facts.batch_id=b.id)->>other.case_revision_id::text)::boolean,false) THEN 'pending' ELSE 'not_required' END)='pending')
     THEN 'pending' ELSE 'complete' END,revision=revision+1 WHERE scope_key=s AND id=b.id;
   INSERT INTO public.evaluation_batch_events(id,batch_id,revision,kind,result_id,evidence,owner_user_id,team_id,created_by)
    SELECT public.gen_random_uuid(),b.id,revision,'human_review_appended',r.id,jsonb_build_object('evaluation_revision',v),owner,team,actor FROM public.evaluation_batches WHERE scope_key=s AND id=b.id;
 ELSE
   v:=(p->>'expected_revision')::bigint; review:=b.review_status;
   IF p->>'kind'='cancel' AND NOT EXISTS(SELECT 1 FROM public.evaluation_judge_intents WHERE scope_key=s AND result_id=r.id AND run_id=(p->>'judge_run_id')::uuid)
   THEN RAISE EXCEPTION 'review_not_found'; END IF;
   IF p->>'kind'='cancel' THEN
     PERFORM n.id FROM public.evaluation_budget_namespaces n JOIN public.evaluation_judge_intents j ON j.scope_key=n.scope_key AND j.namespace_id=n.id
      WHERE j.scope_key=s AND j.run_id=(p->>'judge_run_id')::uuid FOR UPDATE OF n;
   END IF;
 END IF;
 command_id:=public.gen_random_uuid();
 receipt:=jsonb_build_object('id',command_id,'result_id',r.id,'kind',p->>'kind','status',CASE WHEN p->>'kind'='human' THEN 'accepted' ELSE 'queued' END,
   'evaluation_revision',v,'result_revision',r.revision+CASE WHEN p->>'kind'='human' THEN 1 ELSE 0 END,'review_status',review,'judge_run_id',p->>'judge_run_id','error',NULL);
 INSERT INTO public.evaluation_review_commands(id,result_id,batch_id,kind,request_id,fingerprint,principal,payload,receipt,status,judge_run_id,owner_user_id,team_id,created_by)
  VALUES(command_id,r.id,b.id,p->>'kind',p->>'request_id',p->>'fingerprint',principal,p,receipt,CASE WHEN p->>'kind'='human' THEN 'accepted' ELSE 'queued' END,(p->>'judge_run_id')::uuid,owner,team,actor);
 RETURN receipt;
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
            f"""CREATE TABLE evaluation_review_states(result_id uuid NOT NULL,batch_id uuid NOT NULL,rubric_id uuid NOT NULL,status varchar(16) NOT NULL CHECK(status IN ('not_required','pending','complete')),required_dimensions jsonb NOT NULL,applicable_dimensions jsonb NOT NULL,evaluation_revision bigint NOT NULL,{SCOPE},PRIMARY KEY(scope_key,result_id,rubric_id),FOREIGN KEY(scope_key,result_id) REFERENCES evaluation_batch_results(scope_key,id),FOREIGN KEY(scope_key,rubric_id) REFERENCES evaluation_rubric_versions(scope_key,id))"""
        )
    )
    bind.execute(
        sa.text(
            f"""CREATE TABLE evaluation_review_commands(id uuid NOT NULL,result_id uuid NOT NULL,batch_id uuid NOT NULL,kind varchar(8) NOT NULL CHECK(kind IN ('human','rescore','cancel')),request_id varchar(255) NOT NULL,fingerprint varchar(64) NOT NULL,principal jsonb NOT NULL,payload jsonb NOT NULL,receipt jsonb NOT NULL,status varchar(16) NOT NULL CHECK(status IN ('accepted','queued','processing','submitted','cancelling','failed','completed','cancelled')),judge_run_id uuid,generation bigint NOT NULL DEFAULT 0,claim_until timestamptz,error varchar(128),{SCOPE},PRIMARY KEY(scope_key,id),UNIQUE(scope_key,result_id,kind,request_id),FOREIGN KEY(scope_key,result_id) REFERENCES evaluation_batch_results(scope_key,id))"""
        )
    )
    bind.execute(
        sa.text(
            f"""CREATE TABLE evaluation_review_cancellations(command_id uuid NOT NULL,run_id uuid NOT NULL,result_id uuid NOT NULL,principal jsonb NOT NULL,{SCOPE},PRIMARY KEY(scope_key,command_id),FOREIGN KEY(scope_key,command_id) REFERENCES evaluation_review_commands(scope_key,id),FOREIGN KEY(scope_key,run_id) REFERENCES evaluation_judge_intents(scope_key,run_id))"""
        )
    )
    bind.execute(sa.text("ALTER TABLE evaluation_review_cancellations ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE evaluation_review_cancellations FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON evaluation_review_cancellations FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(f"GRANT SELECT,INSERT ON evaluation_review_cancellations TO {quote(kernel)}")
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e09_cancellation_kernel ON evaluation_review_cancellations TO {quote(kernel)} USING (public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel') WITH CHECK (public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel')"
        )
    )
    bind.execute(
        sa.text(
            f"""CREATE TABLE evaluation_review_requirements(batch_id uuid NOT NULL,suite_id uuid NOT NULL,rubric_id uuid NOT NULL,requirements jsonb NOT NULL,{SCOPE},PRIMARY KEY(scope_key,batch_id),FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id))"""
        )
    )
    valid = "public.opencitadel_authorization_valid()"
    kernel_claim = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    scoped = "current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
    for table in (
        "evaluation_review_states",
        "evaluation_review_commands",
        "evaluation_review_requirements",
    ):
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e09_read ON {table} FOR SELECT USING ({valid} AND (({kernel_claim}) OR ({scoped})))"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY e09_kernel ON {table} TO {quote(kernel)} USING ({valid} AND ({kernel_claim})) WITH CHECK ({valid} AND ({kernel_claim}))"
            )
        )
        bind.execute(sa.text(f"REVOKE ALL ON {table} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT ON {table} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE ON {table} TO {quote(kernel)}"))
    bind.execute(sa.text(f"REVOKE UPDATE ON evaluation_review_requirements FROM {quote(kernel)}"))
    # FORCE RLS also applies to the NOBYPASS table/function owner. Only this
    # definer's owner receives these scoped command policies; runtime roles do not.
    for table in (
        "evaluation_review_states",
        "evaluation_review_commands",
        "evaluation_review_requirements",
        "evaluation_score_heads",
        "evaluation_score_sets",
        "evaluation_scores",
        "evaluation_batches",
        "evaluation_batch_results",
        "evaluation_batch_events",
    ):
        bind.execute(
            sa.text(
                f"CREATE POLICY e09_command_owner ON {table} TO {quote(owner)} USING ({valid} AND ({scoped}) AND current_setting('app.is_auditor',true)='false') WITH CHECK ({valid} AND ({scoped}) AND current_setting('app.is_auditor',true)='false')"
            )
        )
    # Private intent access only inside the typed command, for exact rescore proof/cancel binding.
    bind.execute(
        sa.text(
            f"CREATE POLICY e09_intent_owner ON evaluation_judge_intents FOR SELECT TO {quote(owner)} USING ({valid} AND ({scoped}))"
        )
    )
    bind.execute(
        sa.text(f"""CREATE POLICY e09_source_owner ON users FOR SELECT TO {quote(owner)} USING (
      public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'
      AND EXISTS(SELECT 1 FROM public.evaluation_batches source_batch WHERE source_batch.scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END AND source_batch.id::text=current_setting('app.e09_source_batch',true) AND source_batch.principal->>'user_id'=users.id))""")
    )
    bind.execute(
        sa.text(f"""CREATE POLICY e09_source_membership_owner ON team_members FOR SELECT TO {quote(owner)} USING (
      public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='user'
      AND team_id=current_setting('app.team_id',true)
      AND EXISTS(SELECT 1 FROM public.evaluation_batches source_batch WHERE source_batch.scope_key='team:'||current_setting('app.team_id',true) AND source_batch.id::text=current_setting('app.e09_source_batch',true) AND source_batch.principal->>'user_id'=team_members.user_id))""")
    )
    bind.execute(sa.text(COMMAND))
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_e09_command(text,text) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e09_command(text,text) TO {quote(api)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX ix_e09_ready ON evaluation_review_commands(created_at,id) WHERE status IN ('queued','processing')"
        )
    )
    bind.execute(
        sa.text(
            "CREATE INDEX ix_e09_cancel_fence ON evaluation_review_commands(scope_key,judge_run_id) WHERE kind='cancel'"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION public.opencitadel_e09_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF (to_jsonb(OLD)-ARRAY['scope_key','receipt','status','judge_run_id','generation','claim_until','error']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['scope_key','receipt','status','judge_run_id','generation','claim_until','error']) THEN RAISE EXCEPTION 'review_command_immutable'; END IF;
      RETURN NEW; END $$""")
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e09_immutable BEFORE UPDATE ON evaluation_review_commands FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e09_immutable()"
        )
    )


def downgrade():
    raise RuntimeError("review history cannot be discarded")
