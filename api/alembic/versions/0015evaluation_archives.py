"""Scoped immutable archives; evidence and pins remain retained and readable."""

import sqlalchemy as sa

from alembic import op

revision = "0015evaluation_archives"
down_revision = "0014evaluation_summary"
branch_labels = None
depends_on = None

ARCHIVE = r"""
CREATE FUNCTION public.opencitadel_e12_archive(encoded text, signature text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; principal jsonb; actor text; team text; owner text; s text; secret text;
mac bytea; supplied bytea; difference integer:=0; i integer; target uuid; k text;
actual_revision bigint; prior record; b record; receipt jsonb; child uuid; proof text; proof_signature text;
BEGIN
 IF NOT public.opencitadel_authorization_valid() OR current_setting('app.auth_mode',true) IS DISTINCT FROM 'user'
   OR current_setting('app.is_auditor',true) IS DISTINCT FROM 'false'
   OR encoded IS NULL OR octet_length(encoded)>1048576 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
 mac:=public.hmac(convert_to('opencitadel:e12:archive:v1:'||encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256');
 supplied:=decode(signature,'hex');
 FOR i IN 0..31 LOOP difference:=difference | (get_byte(mac,i) # get_byte(supplied,i)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 p:=encoded::jsonb; principal:=p->'principal'; actor:=principal->>'user_id';
 team:=NULLIF(current_setting('app.team_id',true),''); owner:=CASE WHEN team IS NULL THEN actor ELSE NULL END;
 s:=CASE WHEN team IS NULL THEN 'user:'||actor ELSE 'team:'||team END;
 IF actor IS DISTINCT FROM current_setting('app.user_id',true) OR p->>'scope' IS DISTINCT FROM s
   OR p->>'request_id' IS DISTINCT FROM current_setting('app.request_id',true)
   OR p->>'kind' NOT IN ('dataset','config','rubric','suite','recording','environment','batch') OR length(trim(p->>'request_id')) NOT BETWEEN 1 AND 255
 THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 PERFORM id FROM public.users WHERE id=actor FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active' AND token_version=(principal->>'token_version')::integer AND global_role=principal->>'global_role' AND global_role<>'auditor')
 THEN RAISE EXCEPTION 'archive_authorization_revoked'; END IF;
 IF team IS NOT NULL THEN
   PERFORM user_id FROM public.team_members WHERE user_id=actor AND team_id=team FOR SHARE;
   IF NOT EXISTS(SELECT 1 FROM public.team_members WHERE user_id=actor AND team_id=team AND role=principal->'team_roles'->>team)
   THEN RAISE EXCEPTION 'archive_authorization_revoked'; END IF;
 END IF;
 IF p->>'authorization_signature' IS DISTINCT FROM current_setting('app.auth_signature',true)
 OR (p->>'expires')::numeric < extract(epoch FROM clock_timestamp()) THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 target:=(p->>'identity')::uuid; k:=p->>'kind';
 PERFORM pg_advisory_xact_lock(hashtextextended('e12-request:'||s||':'||(p->>'request_id'),0));
 SELECT * INTO prior FROM public.evaluation_archive_commands WHERE scope_key=s AND request_id=p->>'request_id';
 IF FOUND THEN
  IF prior.fingerprint IS DISTINCT FROM p->>'fingerprint' THEN RAISE EXCEPTION 'archive_conflict'; END IF;
  RETURN prior.receipt || jsonb_build_object('new',false);
 END IF;
 CASE k
 WHEN 'dataset' THEN SELECT revision INTO actual_revision FROM public.evaluation_datasets WHERE scope_key=s AND id=target FOR UPDATE;
 WHEN 'config','rubric','suite' THEN SELECT revision INTO actual_revision FROM public.evaluation_configuration_drafts WHERE scope_key=s AND id=target AND kind=k FOR UPDATE;
 WHEN 'recording' THEN
  SELECT revision INTO actual_revision FROM public.evaluation_recording_jobs WHERE scope_key=s AND id=target FOR UPDATE;
  IF EXISTS(SELECT 1 FROM public.evaluation_recording_jobs WHERE scope_key=s AND id=target AND status IN ('queued','running')) THEN RAISE EXCEPTION 'archive_busy'; END IF;
 WHEN 'environment' THEN
  SELECT max(revision) INTO actual_revision FROM public.evaluation_environment_registry WHERE scope_key=s AND id=target AND kind='environment';
 WHEN 'batch' THEN
  SELECT * INTO b FROM public.evaluation_batches WHERE scope_key=s AND id=target FOR UPDATE;
  actual_revision:=b.revision;
  IF b.status NOT IN ('completed','completed_with_errors','failed','cancelled','rejected') OR b.cleanup_status<>'clean'
   OR EXISTS(SELECT 1 FROM public.evaluation_batch_results WHERE scope_key=s AND batch_id=target AND (unknown_effect OR recovery_pending))
   OR EXISTS(SELECT 1 FROM public.evaluation_judge_intents j JOIN public.evaluation_judge_work w ON w.scope_key=j.scope_key AND w.intent_id=j.id WHERE j.scope_key=s AND j.batch_id=target AND w.status NOT IN ('settled','stopped'))
   OR EXISTS(SELECT 1 FROM public.evaluation_review_commands WHERE scope_key=s AND batch_id=target AND status NOT IN ('completed','failed','cancelled') AND NOT (kind='human' AND status='accepted'))
   OR EXISTS(SELECT 1 FROM public.evaluation_budget_namespaces n WHERE n.scope_key=s AND (n.id=target OR n.id IN (SELECT j.namespace_id FROM public.evaluation_judge_intents j WHERE j.scope_key=s AND j.batch_id=target)) AND n.state<>'closed')
   OR EXISTS(SELECT 1 FROM public.evaluation_judge_invalidations x JOIN public.evaluation_judge_intents j ON j.scope_key=x.scope_key AND j.id=x.intent_id WHERE j.scope_key=s AND j.batch_id=target)
   OR EXISTS(SELECT 1 FROM public.execution_model_dispatches d JOIN public.evaluation_judge_intents j ON j.scope_key=d.scope_key AND j.run_id=d.run_id
      LEFT JOIN public.evaluation_budget_reservations r ON r.scope_key=d.scope_key AND r.call_identity::text=d.call_identity
      LEFT JOIN public.execution_model_settlements settled ON settled.scope_key=d.scope_key AND settled.call_identity=d.call_identity
      WHERE j.scope_key=s AND j.batch_id=target AND (r.state='unknown' OR settled.call_identity IS NULL))
   OR EXISTS(SELECT 1 FROM public.evaluation_execution_leases l JOIN public.evaluation_judge_intents j ON j.scope_key=l.scope_key AND j.run_id=l.run_id WHERE j.scope_key=s AND j.batch_id=target AND (l.phase<>'released' OR l.state->>'failure_code'='NON_IDEMPOTENT_OUTCOME_UNKNOWN'
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'settled_activities','[]'::jsonb)) x WHERE x->>1='unknown')
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'activity_failure_codes','[]'::jsonb)) x WHERE x->>2='NON_IDEMPOTENT_OUTCOME_UNKNOWN')))
   OR EXISTS(SELECT 1 FROM public.evaluation_budget_reservations WHERE scope_key=s AND demand->>'batch_id'=target::text AND state<>'settled')
   OR EXISTS(SELECT 1 FROM public.evaluation_environment_leases WHERE scope_key=s AND case_slot->>'batch_id'=target::text AND state<>'verified_clean')
  THEN RAISE EXCEPTION 'archive_busy'; END IF;
  FOR child IN SELECT a.run_id FROM public.evaluation_batch_attempts a JOIN public.evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id WHERE r.scope_key=s AND r.batch_id=target LOOP
   proof:=jsonb_build_object('version',1,'kind','user','scope',s,'run_id',child,'principal',principal)::text;
   proof_signature:=encode(public.hmac(convert_to('opencitadel:e05:requester:v1:'||proof,'UTF8'),convert_to(secret,'UTF8'),'sha256'),'hex');
   IF public.opencitadel_e06_effect_unsafe(s,child,true,proof,proof_signature) THEN RAISE EXCEPTION 'archive_busy'; END IF;
  END LOOP;
 ELSE RAISE EXCEPTION 'archive_kind_invalid';
 END CASE;
 PERFORM pg_advisory_xact_lock(hashtextextended('e12:'||s,0));
 IF k='environment' THEN
  SELECT max(revision) INTO actual_revision FROM public.evaluation_environment_registry WHERE scope_key=s AND id=target AND kind='environment';
  IF EXISTS(SELECT 1 FROM public.evaluation_environment_leases WHERE scope_key=s AND environment_version=target AND state<>'verified_clean')
   OR EXISTS(SELECT 1 FROM public.evaluation_batches batch JOIN public.evaluation_suite_versions suite ON suite.scope_key=batch.scope_key AND suite.id=batch.suite_version
    WHERE batch.scope_key=s AND (batch.status NOT IN ('completed','completed_with_errors','failed','cancelled','rejected') OR batch.cleanup_status<>'clean')
     AND (suite.body->>'environment_version'=target::text OR EXISTS(SELECT 1 FROM public.evaluation_suite_configs member JOIN public.evaluation_config_versions config ON config.scope_key=member.scope_key AND config.id=member.config_version
       WHERE member.scope_key=s AND member.suite_version=suite.id AND config.body->'selection'->'external_contract_ref'->>'kind'='environment' AND config.body->'selection'->'external_contract_ref'->>'version_id'=target::text)))
  THEN RAISE EXCEPTION 'archive_busy'; END IF;
 END IF;
 IF actual_revision IS NULL THEN RAISE EXCEPTION 'archive_not_found'; END IF;
 IF actual_revision IS DISTINCT FROM (p->>'expected_revision')::bigint THEN RAISE EXCEPTION 'archive_conflict'; END IF;
 SELECT * INTO prior FROM public.evaluation_resource_archives WHERE scope_key=s AND kind=k AND resource_id=target;
 IF FOUND THEN
  INSERT INTO public.evaluation_archive_commands(scope_key,request_id,fingerprint,receipt) VALUES(s,p->>'request_id',p->>'fingerprint',prior.receipt);
  RETURN prior.receipt || jsonb_build_object('new',false);
 END IF;
 receipt:=jsonb_build_object('kind',k,'resource_id',target,'revision',actual_revision,'state','archived','retained',true);
 INSERT INTO public.evaluation_resource_archives(scope_key,kind,resource_id,revision,request_id,fingerprint,created_by,receipt)
 VALUES(s,k,target,actual_revision,p->>'request_id',p->>'fingerprint',actor,receipt);
 INSERT INTO public.evaluation_archive_commands(scope_key,request_id,fingerprint,receipt) VALUES(s,p->>'request_id',p->>'fingerprint',receipt);
 RETURN receipt || jsonb_build_object('new',true);
END $$
"""


GUARDS = r"""
CREATE FUNCTION public.opencitadel_e12_active(s text,k text,target uuid,versioned boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE identity uuid:=target; v record; linked uuid;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('e12:'||s,0));
 IF versioned THEN
  CASE k
   WHEN 'dataset' THEN SELECT dataset_id INTO identity FROM public.evaluation_dataset_versions WHERE scope_key=s AND id=target;
   WHEN 'config' THEN SELECT entity_id INTO identity FROM public.evaluation_config_versions WHERE scope_key=s AND id=target;
   WHEN 'rubric' THEN SELECT entity_id INTO identity FROM public.evaluation_rubric_versions WHERE scope_key=s AND id=target;
   WHEN 'suite' THEN SELECT entity_id INTO identity FROM public.evaluation_suite_versions WHERE scope_key=s AND id=target;
   WHEN 'recording' THEN SELECT job_id INTO identity FROM public.evaluation_recording_versions WHERE scope_key=s AND id=target;
   ELSE NULL;
  END CASE;
 END IF;
 IF EXISTS(SELECT 1 FROM public.evaluation_resource_archives WHERE scope_key=s AND kind=k AND resource_id=identity)
 THEN RAISE EXCEPTION 'evaluation_resource_archived'; END IF;
 IF versioned AND k='suite' THEN
  SELECT * INTO v FROM public.evaluation_suite_versions WHERE scope_key=s AND id=target;
  PERFORM public.opencitadel_e12_active(s,'dataset',v.dataset_version,true);
  PERFORM public.opencitadel_e12_active(s,'rubric',v.rubric_version,true);
  FOR linked IN SELECT value::uuid FROM jsonb_array_elements_text(v.body->'recording_versions') LOOP PERFORM public.opencitadel_e12_active(s,'recording',linked,true); END LOOP;
  IF v.body->>'environment_version' IS NOT NULL THEN PERFORM public.opencitadel_e12_active(s,'environment',(v.body->>'environment_version')::uuid); END IF;
  FOR linked IN SELECT config_version FROM public.evaluation_suite_configs WHERE scope_key=s AND suite_version=target LOOP
   PERFORM public.opencitadel_e12_active(s,'config',linked,true);
  END LOOP;
 ELSIF versioned AND k='rubric' THEN
  SELECT judge_config_version INTO linked FROM public.evaluation_rubric_versions WHERE scope_key=s AND id=target;
  PERFORM public.opencitadel_e12_active(s,'config',linked,true);
 ELSIF versioned AND k='config' THEN
  SELECT body INTO v FROM public.evaluation_config_versions WHERE scope_key=s AND id=target;
  IF v.body->'selection'->'external_contract_ref'->>'kind' IN ('recording','environment') THEN
   PERFORM public.opencitadel_e12_active(s,v.body->'selection'->'external_contract_ref'->>'kind',(v.body->'selection'->'external_contract_ref'->>'version_id')::uuid,true);
  END IF;
 END IF;
END $$;
CREATE FUNCTION public.opencitadel_e12_admission_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; body jsonb; linked uuid;
BEGIN
 -- Accepted kernel work retains its fixed history; only new user requests are fenced.
 IF NOT public.opencitadel_authorization_valid() THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 IF current_setting('app.auth_mode',true)<>'user' THEN RETURN NEW; END IF;
 s:=CASE WHEN NEW.team_id IS NULL THEN 'user:'||NEW.owner_user_id ELSE 'team:'||NEW.team_id END;
 body:=to_jsonb(NEW);
 CASE TG_TABLE_NAME
 WHEN 'evaluation_batches' THEN
  PERFORM public.opencitadel_e12_active(s,'suite',NEW.suite_version,true);
  IF NEW.parent_batch IS NOT NULL THEN PERFORM public.opencitadel_e12_active(s,'batch',NEW.parent_batch); END IF;
 WHEN 'evaluation_dataset_versions' THEN PERFORM public.opencitadel_e12_active(s,'dataset',NEW.dataset_id);
 WHEN 'evaluation_configuration_drafts' THEN PERFORM public.opencitadel_e12_active(s,NEW.kind,NEW.id);
 WHEN 'evaluation_config_versions' THEN
  PERFORM public.opencitadel_e12_active(s,'config',NEW.entity_id);
  IF NEW.body->'selection'->'external_contract_ref'->>'kind' IN ('recording','environment') THEN
   PERFORM public.opencitadel_e12_active(s,NEW.body->'selection'->'external_contract_ref'->>'kind',(NEW.body->'selection'->'external_contract_ref'->>'version_id')::uuid,true);
  END IF;
 WHEN 'evaluation_rubric_versions' THEN
  PERFORM public.opencitadel_e12_active(s,'rubric',NEW.entity_id);
  PERFORM public.opencitadel_e12_active(s,'config',NEW.judge_config_version,true);
 WHEN 'evaluation_suite_versions' THEN
  PERFORM public.opencitadel_e12_active(s,'suite',NEW.entity_id);
  PERFORM public.opencitadel_e12_active(s,'dataset',NEW.dataset_version,true);
  PERFORM public.opencitadel_e12_active(s,'rubric',NEW.rubric_version,true);
  FOR linked IN SELECT value::uuid FROM jsonb_array_elements_text(NEW.body->'recording_versions') LOOP PERFORM public.opencitadel_e12_active(s,'recording',linked,true); END LOOP;
  IF NEW.body->>'environment_version' IS NOT NULL THEN PERFORM public.opencitadel_e12_active(s,'environment',(NEW.body->>'environment_version')::uuid); END IF;
  FOR linked IN SELECT value::uuid FROM jsonb_array_elements_text(NEW.body->'config_versions') LOOP PERFORM public.opencitadel_e12_active(s,'config',linked,true); END LOOP;
 WHEN 'evaluation_environment_registry' THEN
  IF NEW.kind='environment' THEN PERFORM public.opencitadel_e12_active(s,'environment',NEW.id); END IF;
 WHEN 'evaluation_review_commands' THEN
  IF NEW.kind='rescore' THEN
   PERFORM public.opencitadel_e12_active(s,'batch',NEW.batch_id);
   PERFORM public.opencitadel_e12_active(s,'rubric',(NEW.payload->>'rubric_version')::uuid,true);
  END IF;
 ELSE RAISE EXCEPTION 'archive_guard_invalid';
 END CASE;
 RETURN NEW;
END $$;
"""


MUTATION_GUARDS = r"""
CREATE FUNCTION public.opencitadel_e12_dataset_mutation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rowdata jsonb; s text; target uuid;
BEGIN
 IF NOT public.opencitadel_authorization_valid() THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 IF current_setting('app.auth_mode',true)<>'user' THEN
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END IF;
 rowdata:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 s:=CASE WHEN rowdata->>'team_id' IS NULL THEN 'user:'||(rowdata->>'owner_user_id') ELSE 'team:'||(rowdata->>'team_id') END;
 target:=CASE WHEN TG_TABLE_NAME='evaluation_datasets' THEN (rowdata->>'id')::uuid ELSE (rowdata->>'dataset_id')::uuid END;
 IF TG_OP='UPDATE' AND TG_TABLE_NAME<>'evaluation_datasets' THEN
  IF NEW.dataset_id IS DISTINCT FROM OLD.dataset_id THEN RAISE EXCEPTION 'dataset_membership_identity_changed'; END IF;
 END IF;
 -- Every member mutation takes the same parent-row then archive-scope order.
 PERFORM id FROM public.evaluation_datasets WHERE scope_key=s AND id=target FOR UPDATE;
 PERFORM public.opencitadel_e12_active(s,'dataset',target);
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE FUNCTION public.opencitadel_e12_environment_allocation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text;
BEGIN
 IF NOT public.opencitadel_authorization_valid() THEN RAISE EXCEPTION 'archive_authorization_invalid'; END IF;
 s:=CASE WHEN NEW.team_id IS NULL THEN 'user:'||NEW.owner_user_id ELSE 'team:'||NEW.team_id END;
 PERFORM public.opencitadel_e12_active(s,'environment',NEW.environment_version);
 RETURN NEW;
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
    bind.execute(
        sa.text(
            """CREATE TABLE evaluation_resource_archives(scope_key varchar(261) NOT NULL,kind varchar(20) NOT NULL CHECK(kind IN ('dataset','config','rubric','suite','recording','environment','batch')),resource_id uuid NOT NULL,revision bigint NOT NULL,request_id varchar(255) NOT NULL,fingerprint varchar(64) NOT NULL,created_by varchar(255) NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),receipt jsonb NOT NULL,PRIMARY KEY(scope_key,kind,resource_id),UNIQUE(scope_key,request_id))"""
        )
    )
    bind.execute(
        sa.text(
            "CREATE TABLE evaluation_archive_commands(scope_key varchar(261) NOT NULL,request_id varchar(255) NOT NULL,fingerprint varchar(64) NOT NULL,receipt jsonb NOT NULL,PRIMARY KEY(scope_key,request_id))"
        )
    )
    valid = "public.opencitadel_authorization_valid()"
    scoped = "current_setting('app.auth_mode',true)='user' AND scope_key=CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>'' THEN 'team:'||current_setting('app.team_id',true) ELSE 'user:'||current_setting('app.user_id',true) END"
    system = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    bind.execute(sa.text("ALTER TABLE evaluation_resource_archives ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE evaluation_resource_archives FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON evaluation_resource_archives FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(f"GRANT SELECT ON evaluation_resource_archives TO {quote(api)},{quote(kernel)}")
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e12_archive_read ON evaluation_resource_archives FOR SELECT USING ({valid} AND (({scoped}) OR ({system})))"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e12_archive_owner ON evaluation_resource_archives FOR INSERT TO {quote(owner)} WITH CHECK ({valid} AND ({scoped}) AND current_setting('app.is_auditor',true)='false')"
        )
    )
    for table in (
        "evaluation_recording_jobs",
        "evaluation_configuration_drafts",
        "evaluation_environment_leases",
        "evaluation_judge_work",
        "evaluation_budget_reservations",
        "evaluation_budget_namespaces",
        "evaluation_execution_leases",
        "evaluation_judge_invalidations",
        "execution_model_dispatches",
        "execution_model_settlements",
    ):
        bind.execute(
            sa.text(
                f"CREATE POLICY e12_archive_owner ON {table} FOR SELECT TO {quote(owner)} USING ({valid} AND ({scoped}))"
            )
        )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e12_archive_immutable BEFORE UPDATE OR DELETE ON evaluation_resource_archives FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e01_immutable()"
        )
    )
    bind.execute(sa.text("ALTER TABLE evaluation_archive_commands ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE evaluation_archive_commands FORCE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            f"REVOKE ALL ON evaluation_archive_commands FROM PUBLIC,{quote(api)},{quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e12_command_owner ON evaluation_archive_commands TO {quote(owner)} USING ({valid} AND ({scoped})) WITH CHECK ({valid} AND ({scoped}) AND current_setting('app.is_auditor',true)='false')"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e12_command_immutable BEFORE UPDATE OR DELETE ON evaluation_archive_commands FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e01_immutable()"
        )
    )
    bind.execute(sa.text(ARCHIVE))
    bind.execute(sa.text(GUARDS))
    bind.execute(sa.text(MUTATION_GUARDS))
    for table, event in (
        ("evaluation_datasets", "UPDATE OR DELETE"),
        ("evaluation_draft_cases", "INSERT OR UPDATE OR DELETE"),
        ("evaluation_case_revisions", "INSERT"),
        ("evaluation_imports", "INSERT OR UPDATE"),
    ):
        bind.execute(
            sa.text(
                f"CREATE TRIGGER e12_dataset_mutation_guard BEFORE {event} ON {table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e12_dataset_mutation_guard()"
            )
        )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e12_environment_allocation_guard BEFORE INSERT ON evaluation_environment_leases FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e12_environment_allocation_guard()"
        )
    )
    for name in (
        "opencitadel_e12_active(text,text,uuid,boolean)",
        "opencitadel_e12_admission_guard()",
        "opencitadel_e12_dataset_mutation_guard()",
        "opencitadel_e12_environment_allocation_guard()",
    ):
        bind.execute(sa.text(f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC"))
    for table in (
        "evaluation_batches",
        "evaluation_dataset_versions",
        "evaluation_configuration_drafts",
        "evaluation_config_versions",
        "evaluation_rubric_versions",
        "evaluation_suite_versions",
        "evaluation_environment_registry",
        "evaluation_review_commands",
    ):
        event = "INSERT OR UPDATE" if table == "evaluation_configuration_drafts" else "INSERT"
        bind.execute(
            sa.text(
                f"CREATE TRIGGER e12_admission_guard BEFORE {event} ON {table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e12_admission_guard()"
            )
        )
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_e12_archive(text,text) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e12_archive(text,text) TO {quote(api)}"
        )
    )


def downgrade():
    raise RuntimeError("immutable archive facts cannot be downgraded")
