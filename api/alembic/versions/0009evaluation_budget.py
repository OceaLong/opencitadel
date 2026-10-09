"""Atomic shared capacity and conservative physical-call reservations."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0009evaluation_budget"
down_revision = "0008evaluation_environments"
branch_labels = None
depends_on = None

EXECUTION_TABLES = {
    "evaluation_work_unit_lineages",
    "evaluation_run_lineages",
    "evaluation_model_logical_calls",
    "evaluation_execution_policy_versions",
    "evaluation_execution_policy_head",
    "evaluation_execution_pools",
    "evaluation_execution_leases",
}
DEPLOYMENT_POLICY_TABLES = {
    "evaluation_physical_policy_versions",
    "evaluation_physical_policy_head",
    "evaluation_environment_capacity_versions",
    "evaluation_environment_capacity_head",
}
CONTROL_TABLES = {"evaluation_budget_namespaces", "evaluation_budget_bindings"}
TABLES = {
    "evaluation_environment_capacity_versions": "revision bigint PRIMARY KEY CHECK(revision>=1), body jsonb NOT NULL",
    "evaluation_environment_capacity_head": "singleton boolean PRIMARY KEY CHECK(singleton), revision bigint NOT NULL REFERENCES evaluation_environment_capacity_versions(revision)",
    "evaluation_physical_policy_versions": "revision bigint PRIMARY KEY CHECK(revision>=1), body jsonb NOT NULL",
    "evaluation_physical_policy_head": "singleton boolean PRIMARY KEY CHECK(singleton), revision bigint NOT NULL REFERENCES evaluation_physical_policy_versions(revision), requester_cutover_at timestamptz NOT NULL DEFAULT clock_timestamp()",
    "evaluation_budget_namespaces": """id uuid PRIMARY KEY, scope_key text NOT NULL,
        body jsonb NOT NULL, revision integer NOT NULL DEFAULT 1 CHECK(revision>=1),
        state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','closed')),
        UNIQUE(id,scope_key)""",
    "evaluation_budget_bindings": """run_id uuid PRIMARY KEY, namespace_id uuid NOT NULL,
        scope_key text NOT NULL, source_entity_id text NOT NULL, purpose text NOT NULL
        CHECK(purpose IN ('evaluation_subject','evaluation_judge')), body jsonb NOT NULL,
        FOREIGN KEY(namespace_id,scope_key) REFERENCES evaluation_budget_namespaces(id,scope_key),
        UNIQUE(namespace_id,source_entity_id,purpose)""",
    "evaluation_execution_policy_versions": """revision bigint PRIMARY KEY CHECK(revision>=1), body jsonb NOT NULL""",
    "evaluation_execution_policy_head": """singleton boolean PRIMARY KEY CHECK(singleton), revision bigint NOT NULL REFERENCES evaluation_execution_policy_versions(revision)""",
    "evaluation_execution_pools": """key text PRIMARY KEY, occupied integer NOT NULL CHECK(occupied>=0)""",
    "evaluation_execution_leases": """run_id uuid PRIMARY KEY REFERENCES evaluation_budget_bindings(run_id),
        namespace_id uuid NOT NULL, scope_key text NOT NULL,
        phase text NOT NULL CHECK(phase IN ('prepared','held','released')),
        generation integer NOT NULL CHECK(generation>=1), accepted_version integer NOT NULL CHECK(accepted_version>=0),
        run_generation integer NOT NULL CHECK(run_generation>=0), policy_revision bigint NOT NULL,
        state jsonb, FOREIGN KEY(namespace_id,scope_key) REFERENCES evaluation_budget_namespaces(id,scope_key)""",
    "evaluation_work_unit_lineages": "id text PRIMARY KEY, namespace_id uuid NOT NULL REFERENCES evaluation_budget_namespaces(id), root_run_id uuid NOT NULL UNIQUE REFERENCES evaluation_budget_bindings(run_id)",
    "evaluation_run_lineages": "run_id uuid PRIMARY KEY REFERENCES evaluation_budget_bindings(run_id), work_unit_id text NOT NULL REFERENCES evaluation_work_unit_lineages(id), root_run_id uuid NOT NULL REFERENCES evaluation_budget_bindings(run_id), predecessor_run_id uuid UNIQUE REFERENCES evaluation_run_lineages(run_id), predecessor_generation integer CHECK(predecessor_generation>=1), CHECK((predecessor_run_id IS NULL)=(predecessor_generation IS NULL))",
    "evaluation_model_logical_calls": """id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES evaluation_budget_bindings(run_id), scope_key text NOT NULL, sends integer NOT NULL DEFAULT 0 CHECK(sends>=0 AND sends<=3)""",
    "evaluation_budget_buckets": """key text PRIMARY KEY, limits jsonb NOT NULL,
        spent_tokens bigint NOT NULL DEFAULT 0 CHECK(spent_tokens>=0),
        reserved_tokens bigint NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
        spent_money numeric NOT NULL DEFAULT 0 CHECK(spent_money>=0),
        reserved_money numeric NOT NULL DEFAULT 0 CHECK(reserved_money>=0),
        slots integer NOT NULL DEFAULT 0 CHECK(slots>=0), breached boolean NOT NULL DEFAULT false""",
    "evaluation_budget_reservations": """call_identity uuid PRIMARY KEY, scope_key text NOT NULL,
        requester text NOT NULL, demand jsonb NOT NULL, state text NOT NULL
        CHECK(state IN ('dispatching','unknown','settled')), settlement jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), settled_at timestamptz""",
}

# The signed envelope is private server-generated operational authority, not a public DTO.
# Exact encoded bytes are signed; PostgreSQL parses them only AFTER verifying the MAC.
FUNCTION = r"""
CREATE OR REPLACE FUNCTION public.opencitadel_e05_operation(encoded text, signature text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    active_policy jsonb; active_revision bigint;
    secret text; expected bytea; supplied bytea; difference integer := 0;
    envelope jsonb; demand jsonb; bucket jsonb; receipt jsonb; operation text;
    identity uuid; scope text; requester text; i integer; result jsonb;
    previous public.evaluation_budget_reservations%ROWTYPE;
    counter public.evaluation_budget_buckets%ROWTYPE;
    tokens bigint; money numeric; actual_tokens bigint; actual_money numeric;
BEGIN
    IF NOT public.opencitadel_authorization_valid() THEN
        RAISE EXCEPTION 'budget_authorization_invalid';
    END IF;
    SELECT signing_secret INTO secret FROM public.execution_authorization_secrets WHERE singleton;
    IF encoded IS NULL OR octet_length(encoded)>65536 OR signature IS NULL OR signature !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'budget_envelope_invalid'; END IF;
    expected := public.hmac(convert_to((CASE WHEN encoded::jsonb->>'operation'='complete_direct' THEN 'opencitadel:e05:direct-completion:v1:' ELSE 'opencitadel:e05:v1:' END) || encoded,'UTF8'),convert_to(secret,'UTF8'),'sha256');
    supplied := decode(signature,'hex');
    FOR i IN 0..31 LOOP
        difference := difference | (get_byte(expected,i) # get_byte(supplied,i));
    END LOOP;
    IF difference<>0 THEN RAISE EXCEPTION 'budget_envelope_invalid'; END IF;
    envelope := encoded::jsonb;
    IF envelope->>'authorization_signature' IS DISTINCT FROM current_setting('app.auth_signature',true) THEN
        RAISE EXCEPTION 'budget_authorization_binding';
    END IF;
    operation := envelope->>'operation'; demand := envelope->'demand';
    identity := (envelope->>'identity')::uuid;
    scope := demand->>'scope'; requester := demand->>'requester';
    IF scope IS NULL OR requester IS NULL OR operation IS NULL OR operation NOT IN ('reserve','settle','unknown','lock','complete_direct') THEN
        RAISE EXCEPTION 'budget_envelope_invalid';
    END IF;
    IF current_setting('app.auth_mode',true)<>'system' AND (
        current_setting('app.auth_mode',true)<>'user' OR
        requester<>current_setting('app.user_id',true) OR
        scope<>CASE WHEN coalesce(current_setting('app.team_id',true),'')<>''
            THEN 'team:' || current_setting('app.team_id',true)
            ELSE 'user:' || current_setting('app.user_id',true) END
    ) THEN RAISE EXCEPTION 'budget_scope_denied'; END IF;
    tokens := (demand->>'tokens')::bigint; money := (demand->>'money')::numeric;
    IF tokens<0 OR money<0 OR jsonb_array_length(demand->'buckets')=0 THEN
        RAISE EXCEPTION 'budget_demand_invalid';
    END IF;
    SELECT h.revision,v.body INTO active_revision,active_policy
        FROM public.evaluation_physical_policy_head h JOIN public.evaluation_physical_policy_versions v ON v.revision=h.revision
        WHERE h.singleton FOR UPDATE OF h;
    IF operation IN ('reserve','lock') AND demand ? 'policy_revision' AND active_revision IS NULL THEN RAISE EXCEPTION 'budget_policy_unavailable'; END IF;
    IF operation IN ('reserve','lock') AND active_revision IS NOT NULL AND (demand->>'policy_revision')::bigint IS DISTINCT FROM active_revision THEN
        RAISE EXCEPTION 'budget_policy_changed';
    END IF;
    -- One universal order for creation, reserve, unknown and settlement. Global
    -- first, then provider/user/workspace/purpose/batch; no claim lock is held here.
    FOR bucket IN SELECT value FROM jsonb_array_elements(demand->'buckets') ORDER BY value->>'key' LOOP
        INSERT INTO public.evaluation_budget_buckets(key,limits)
            VALUES(bucket->>'key',bucket) ON CONFLICT DO NOTHING;
        SELECT * INTO counter FROM public.evaluation_budget_buckets
            WHERE key=bucket->>'key' FOR UPDATE;
        IF active_revision IS NOT NULL AND (bucket->>'key'='0:global' OR bucket->>'key' LIKE '1:provider:%' OR bucket->>'key' LIKE '2:user:%') THEN
            IF operation IN ('reserve','lock') AND (bucket->>'slots')::integer IS DISTINCT FROM (CASE
                WHEN bucket->>'key'='0:global' THEN active_policy->>'global_concurrency'
                WHEN bucket->>'key' LIKE '1:provider:%' THEN active_policy->>'provider_concurrency'
                ELSE active_policy->>'user_concurrency' END)::integer THEN RAISE EXCEPTION 'budget_policy_conflict'; END IF;
            IF operation IN ('reserve','lock') AND counter.limits<>bucket THEN RAISE EXCEPTION 'budget_policy_conflict'; END IF;
        ELSIF counter.limits<>bucket THEN RAISE EXCEPTION 'budget_policy_conflict'; END IF;
    END LOOP;
    -- Expiry is checked after every potentially blocking capacity lock.
    IF envelope->>'expires' IS NULL OR (envelope->>'expires')::numeric<=extract(epoch FROM clock_timestamp()) THEN
        RAISE EXCEPTION 'budget_envelope_expired';
    END IF;
    IF operation<>'complete_direct' AND current_setting('app.auth_mode',true)='user' AND demand ? 'principal' AND (
        (current_setting('app.is_admin',true)='true') IS DISTINCT FROM (demand->'principal'->>'global_role'='admin') OR
        (current_setting('app.is_auditor',true)='true') IS DISTINCT FROM (demand->'principal'->>'global_role'='auditor')
    ) THEN RAISE EXCEPTION 'budget_role_binding'; END IF;
    -- Revalidate current requester after blocking locks. Kernel settlement may
    -- preserve original evidence after cancellation/revocation; it cannot revive a run.
    IF operation<>'complete_direct' AND (current_setting('app.auth_mode',true)='user' OR operation IN ('reserve','lock')) AND demand ? 'principal' THEN
        IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=requester AND status='active'
            AND token_version=(demand->'principal'->>'token_version')::integer
            AND global_role=demand->'principal'->>'global_role') THEN
            RAISE EXCEPTION 'budget_requester_revoked';
        END IF;
        IF scope LIKE 'team:%' AND NOT EXISTS(SELECT 1 FROM public.team_members
            WHERE team_id=substring(scope FROM 6) AND user_id=requester
            AND role=demand->'principal'->>'team_role') THEN
            RAISE EXCEPTION 'budget_membership_revoked';
        END IF;
    ELSIF operation<>'complete_direct' AND current_setting('app.auth_mode',true)='user' THEN
        RAISE EXCEPTION 'budget_requester_proof_required';
    END IF;
    IF operation='lock' THEN RETURN jsonb_build_object('state','locked'); END IF;
    SELECT * INTO previous FROM public.evaluation_budget_reservations
        WHERE call_identity=identity FOR UPDATE;
    IF FOUND AND previous.demand<>demand THEN RAISE EXCEPTION 'budget_identity_conflict'; END IF;
    IF operation='reserve' THEN
        IF previous.call_identity IS NOT NULL THEN
            RETURN jsonb_build_object('fresh',false,'state',previous.state);
        END IF;
        FOR bucket IN SELECT value FROM jsonb_array_elements(demand->'buckets') ORDER BY value->>'key' LOOP
            SELECT * INTO counter FROM public.evaluation_budget_buckets WHERE key=bucket->>'key';
            IF counter.breached THEN RAISE EXCEPTION 'budget_bound_breached'; END IF;
            IF bucket ? 'slots' AND counter.slots+1>(bucket->>'slots')::integer THEN
                RAISE EXCEPTION 'budget_concurrency_exhausted';
            END IF;
            IF bucket ? 'tokens' AND (tokens IS NULL OR
                counter.spent_tokens+counter.reserved_tokens+tokens>(bucket->>'tokens')::bigint) THEN
                RAISE EXCEPTION 'budget_exhausted';
            END IF;
            IF bucket ? 'money' AND (money IS NULL OR
                counter.spent_money+counter.reserved_money+money>(bucket->>'money')::numeric) THEN
                RAISE EXCEPTION 'budget_money_exhausted';
            END IF;
        END LOOP;
        INSERT INTO public.evaluation_budget_reservations(call_identity,scope_key,requester,demand,state)
            VALUES(identity,scope,requester,demand,'dispatching');
        FOR bucket IN SELECT value FROM jsonb_array_elements(demand->'buckets') LOOP
            UPDATE public.evaluation_budget_buckets SET
                reserved_tokens=reserved_tokens+coalesce(tokens,0),
                reserved_money=reserved_money+coalesce(money,0), slots=slots+1
                WHERE key=bucket->>'key';
        END LOOP;
        RETURN jsonb_build_object('fresh',true,'state','dispatching');
    END IF;
    IF previous.call_identity IS NULL THEN RAISE EXCEPTION 'budget_reservation_unavailable'; END IF;
    IF operation='complete_direct' AND (
        demand->'direct_request'->>'request_id' IS DISTINCT FROM identity::text OR
        NOT (demand ? 'principal') OR demand ? 'tokens' OR demand ? 'money'
    ) THEN RAISE EXCEPTION 'budget_direct_receipt_required'; END IF;
    IF operation='unknown' THEN
        IF previous.state<>'settled' THEN
            UPDATE public.evaluation_budget_reservations SET state='unknown' WHERE call_identity=identity;
        END IF;
        RETURN jsonb_build_object('fresh',false,'state',CASE WHEN previous.state='settled' THEN 'settled' ELSE 'unknown' END);
    END IF;
    receipt := envelope->'settlement';
    IF previous.settlement IS NOT NULL THEN
        IF previous.settlement<>receipt THEN RAISE EXCEPTION 'budget_settlement_conflict'; END IF;
        RETURN jsonb_build_object('fresh',false,'state','settled');
    END IF;
    actual_tokens := (receipt->>'tokens')::bigint; actual_money := (receipt->>'money')::numeric;
    IF actual_tokens<0 OR actual_money<0 THEN RAISE EXCEPTION 'budget_settlement_invalid'; END IF;
    FOR bucket IN SELECT value FROM jsonb_array_elements(demand->'buckets') LOOP
        UPDATE public.evaluation_budget_buckets SET
            reserved_tokens=reserved_tokens-CASE WHEN actual_tokens IS NOT NULL THEN coalesce(tokens,0) ELSE 0 END,
            reserved_money=reserved_money-CASE WHEN actual_money IS NOT NULL THEN coalesce(money,0) ELSE 0 END,
            spent_tokens=spent_tokens+coalesce(actual_tokens,0),
            spent_money=spent_money+coalesce(actual_money,0),slots=slots-1,
            breached=breached OR coalesce(actual_tokens>tokens,false) OR coalesce(actual_money>money,false)
            WHERE key=bucket->>'key';
    END LOOP;
    UPDATE public.evaluation_budget_reservations SET state='settled',settlement=receipt,
        settled_at=clock_timestamp() WHERE call_identity=identity;
    RETURN jsonb_build_object('fresh',true,'state','settled');
END $$
"""


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    existing = set(sa.inspect(bind).get_table_names(schema="public")) & TABLES.keys()
    if existing:
        spec = importlib.util.spec_from_file_location(
            "e05_shape", Path(__file__).with_name("0002artifact_provenance.py")
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper._validate_existing(bind, TABLES, [], existing)
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    quote = bind.dialect.identifier_preparer.quote
    owner = bind.scalar(sa.text("SELECT current_user"))
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(kernel)}"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_function_owner ON {name}"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_system_read ON {name}"))
        # The migration login owns only these new objects; it is NOBYPASSRLS.
        # API callers cannot SET ROLE to it or mutate tables directly.
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_function_owner ON {name} TO {quote(owner)} USING (true) WITH CHECK (true)"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_system_read ON {name} FOR SELECT USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system')"
            )
        )
    for name in CONTROL_TABLES:
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(kernel)}"))
        if name == "evaluation_budget_namespaces":
            bind.execute(sa.text(f"GRANT UPDATE(state,revision) ON {name} TO {quote(kernel)}"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_kernel_control ON {name}"))
        predicate = """public.opencitadel_authorization_valid() AND (
            current_setting('app.auth_mode',true)='system' OR (
                current_setting('app.auth_mode',true)='user' AND scope_key = CASE
                    WHEN coalesce(current_setting('app.team_id',true),'')<>''
                    THEN 'team:' || current_setting('app.team_id',true)
                    ELSE 'user:' || current_setting('app.user_id',true) END))"""
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_kernel_control ON {name} TO {quote(kernel)} USING ({predicate}) WITH CHECK ({predicate})"
            )
        )
    for name in EXECUTION_TABLES:
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(kernel)}"))
        columns = {
            "evaluation_model_logical_calls": "sends",
            "evaluation_execution_policy_head": "revision",
            "evaluation_execution_pools": "occupied",
            "evaluation_execution_leases": "phase,generation,accepted_version,run_generation,policy_revision,state",
        }.get(name)
        if columns:
            bind.execute(sa.text(f"GRANT UPDATE({columns}) ON {name} TO {quote(kernel)}"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_kernel_execution ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_kernel_execution ON {name} TO {quote(kernel)} USING(public.opencitadel_authorization_valid()) WITH CHECK(public.opencitadel_authorization_valid())"
            )
        )
    for name in DEPLOYMENT_POLICY_TABLES:
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT INSERT ON {name} TO {quote(kernel)}"))
        if name in {"evaluation_physical_policy_head", "evaluation_environment_capacity_head"}:
            bind.execute(sa.text(f"GRANT UPDATE(revision) ON {name} TO {quote(kernel)}"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_policy_read ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_policy_read ON {name} FOR SELECT USING(public.opencitadel_authorization_valid())"
            )
        )
        bind.execute(sa.text(f"DROP POLICY IF EXISTS e05_policy_operator ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e05_policy_operator ON {name} TO {quote(kernel)} USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system') WITH CHECK(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system')"
            )
        )
    bind.execute(sa.text(f"GRANT UPDATE(limits) ON evaluation_budget_buckets TO {quote(kernel)}"))
    bind.execute(
        sa.text("DROP POLICY IF EXISTS e05_policy_activate_buckets ON evaluation_budget_buckets")
    )
    bind.execute(
        sa.text(
            f"CREATE POLICY e05_policy_activate_buckets ON evaluation_budget_buckets FOR UPDATE TO {quote(kernel)} USING(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system') WITH CHECK(public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system')"
        )
    )
    bind.execute(sa.text(FUNCTION))
    bind.execute(
        sa.text("REVOKE ALL ON FUNCTION public.opencitadel_e05_operation(text,text) FROM PUBLIC")
    )
    bind.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION public.opencitadel_e05_operation(text,text) TO {quote(api)},{quote(kernel)}"
        )
    )


def downgrade():
    raise RuntimeError("budget facts cannot be downgraded")
